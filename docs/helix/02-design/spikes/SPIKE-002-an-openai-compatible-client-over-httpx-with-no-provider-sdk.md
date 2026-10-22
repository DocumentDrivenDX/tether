---
ddx:
  id: SPIKE-002
  type: tech-spike
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-012
      kind: informs
    - id: ADR-002
      kind: informs
    - id: SPIKE-001
      kind: informed_by
    - id: CONTRACT-001
      kind: informs
---

# Technical Spike: An OpenAI-Compatible Client Over `httpx` With No Provider SDK

> Handling: the probe runs against a server the probe starts on the loopback interface. It holds no credential or private data; the bearer values are variables the probe sets, and the "secret content" string is a marker used to test that error bodies do not leak.

**Spike ID**: SPIKE-002 | **Lead**: Erik LaBianca | **Time Budget**: two hours | **Status**: Completed 2026-10-22 (a local test server only; no real model server was available and none was exercised)

## Objective

**Technical Question**: Tether will not depend on a foundation-model provider's SDK (ADR-012). Can a client written directly against the OpenAI-compatible chat-completions protocol, using only `httpx`, carry the model port's requirements, and how large is it?

**Goals**:
- [x] Stream text deltas and assemble tool calls whose arguments arrive in pieces.
- [x] Read token usage when a server sends it, and tolerate a server that does not.
- [x] Carry a per-call credential and the provider's request id.
- [x] Report an HTTP failure and a truncated stream as an outage that never contains the response body.
- [x] Pass server-specific request parameters through unchanged (the sampling and caching knobs local servers expose).
- [x] Parse server-sent events that a server splits mid-line across network chunks.
- [x] Stop the underlying request when the caller cancels.
- [x] Serve concurrent calls with distinct credentials.

**Success Criteria**: Each goal shown against a real HTTP server, with the size of the client stated.

**Out of Scope**: Writing the production adapter (it ports an existing, tested client of this protocol; this probe only shows the approach is feasible over `httpx`), a real local or hosted model server, every server's quirks, the Responses API, and image or audio content.

## Hypothesis

**Primary**: The protocol is small enough that a client over `httpx` is a few dozen lines of core logic and carries every requirement without an SDK.
**Assumptions**: Servers that speak the protocol stream `data:` lines ending in `data: [DONE]`, with tool calls as indexed deltas.
**Expected Outcome**: Feasible; the cost is in the quirks of particular servers, which the adapter's conformance matrix must find.

## Approach

**Method**: Prototype against a standard-library `asyncio` server that streams chunked server-sent events, splits one event across two network chunks, and plays scenarios: a tool call with arguments in pieces and a usage chunk, a reply with no usage, an HTTP 500 with a body, a stream that ends without `[DONE]`, and a slow stream for cancellation.

**Activities**:
| Step | Activity | Objective |
|------|----------|-----------|
| 1 | Check whether Pydantic AI's OpenAI-compatible model needs the `openai` package | Establish whether the earlier default conflicts with ADR-012 |
| 2 | Write the client and the scenario server (`SPIKE-002-probe/compat_client_probe.py.txt`), `httpx` 0.28 on Python 3.11 | Show each goal over a real socket |

## Findings

**FINDING 1**: Pydantic AI's OpenAI-compatible model requires the `openai` package.
- **Evidence**: With only `pydantic-ai-slim` installed, importing `pydantic_ai.models.openai.OpenAIChatModel` raises `ImportError: Please install openai to use the OpenAI model`. The base dependencies are `anyio`, `genai-prices`, `griffelib`, `httpx2`, `opentelemetry-api`, `pydantic-graph`, `pydantic`, and `typing-inspection`.
- **Implications**: Using Pydantic AI for the OpenAI-compatible protocol brings the OpenAI SDK in as its protocol client. That conflicts with ADR-012 unless the OpenAI SDK is an accepted exception, so Pydantic AI cannot be the default adapter.

**FINDING 2**: A client over `httpx` is about 30 lines of core logic and carries every requirement tested.
- **Evidence**: The client function that builds the request, streams, assembles, and reports is 30 non-blank lines. Against the scenario server it returned: the delta `Looking. ` and a tool call `lookup` with arguments `{"q": "sample"}` assembled from two pieces, one of which was split mid-line across network chunks; usage 12 input and 9 output tokens; the request id from the response header.
- **Implications**: The default adapter is small enough to own, read, and test in full.

**FINDING 3**: A server that sends no usage chunk, a failed request, and a truncated stream are handled without leaking content.
- **Evidence**: A reply with no usage chunk returned usage `None`. A 500 reply whose body contained `secret content` raised an outage with reason `http 500` and the response's request id, and the reason did not contain the body. A stream that ended without `[DONE]` raised `incomplete stream`.
- **Implications**: The port's outage rule (CONTRACT-001) holds by construction, since the client never reads the error body into an exception.

**FINDING 4**: Server-specific parameters pass through, and the per-call credential and concurrency work.
- **Evidence**: The request body at the server contained `min_p: 0.05` and `cache_prompt: true` as supplied through the pass-through parameters, and `stream_options.include_usage: true`. Five concurrent calls, each with its own credential object, each read back the request id derived from its own credential.
- **Implications**: Sampling, caching, and grammar parameters that local servers expose can be offered without the port giving them any meaning.

**FINDING 5**: Cancelling the caller closes the connection.
- **Evidence**: Cancelling a task consuming a slow stream made the server see a disconnect within 0.3 s.
- **Implications**: A cancelled turn stops its model request, as CONTRACT-001 requires.

### Measurements
| Requirement | Result | Notes |
|-------------|--------|-------|
| Streaming and tool-call assembly | Works | Arguments in pieces; an event split across chunks |
| Usage present or absent | Works | `None` when absent |
| Outage without body | Works by construction | Status or class name only |
| Truncated stream | Detected | Missing `[DONE]` |
| Per-call credential | Works | One header per call |
| Server-specific parameters | Works | Merged into the body |
| Concurrency | Works | Five calls, five identities |
| Cancellation | Works | The connection closes |
| Size of the client | About 30 lines of logic | The full adapter adds message and tool mapping and configuration |

## Analysis

**Hypothesis**: CONFIRMED
**Rationale**: Every requirement held against a real socket, and the client is small.

### Risks
| Risk | Prob | Impact | Mitigation |
|------|------|--------|------------|
| Real servers differ from the test server (tool-call streaming, finish reasons, reasoning fields, partial JSON, missing fields) | H | M | A conformance matrix of real local servers, recorded as cassettes on fabricated prompts and run on demand; a quirk table in the adapter; the optional Pydantic AI adapter as a fallback |
| Some servers return chain-of-thought in a separate field | M | M | The adapter ignores or surfaces it by configuration; decided in the slice |
| The owned adapter lags a protocol change | L | M | The protocol is stable and small; the matrix finds drift |

## Conclusions

**Primary Conclusion**: The default model adapter can be tether's own client over `httpx`, speaking the OpenAI-compatible protocol, with no provider SDK.
**Confidence**: Medium. The protocol behavior is shown over a real socket against a test server; real servers' quirks are not.
**Limitations**: No real model server was exercised. Compatibility with any named server is a claim for the adapter's conformance matrix to establish, not this spike.

## Recommendations

**RECOMMENDATION**: Make the default model adapter an owned `httpx` client of the OpenAI-compatible protocol, keep Pydantic AI as an optional adapter, and record the decision in ADR-002 under the goal of ADR-012.
- **Rationale**: It meets the goal with no SDK (Finding 1), is small enough to own (Finding 2), gives exact control over errors, parameters, and cancellation (Findings 3 to 5), and keeps local and sovereign servers first-class.
- **Next Steps**: Slice 1 ports the first downstream platform's tested client of this protocol (its request and reply mapping, stream assembly, and error diagnostics, and its tests) to asynchronous `httpx`, rather than writing one from this probe, and builds the conformance matrix; a real local server is added to the matrix when one is available.
- **Concern Impact**: Adds a project-local concern, model provider independence, recorded in `concerns.md`.

## Artifacts

- `SPIKE-002-probe/compat_client_probe.py.txt`: the client and the scenario server, run with `uv run --no-project --python 3.11 --with httpx python compat_client_probe.py` after renaming to `.py`.
