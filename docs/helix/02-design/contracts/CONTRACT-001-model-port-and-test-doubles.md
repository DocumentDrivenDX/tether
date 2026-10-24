---
ddx:
  id: CONTRACT-001
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-24
  authoring:
    home: repo
  links:
    - id: ADR-001
      kind: informed_by
    - id: ADR-002
      kind: informed_by
    - id: ADR-012
      kind: informed_by
    - id: SPIKE-001
      kind: informed_by
    - id: SPIKE-002
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: CONTRACT-004
      kind: informed_by
    - id: architecture
      kind: informed_by
---

# CONTRACT-001: Model Port and Test Doubles

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-001
**Type**: library (Python port)
**Version**: v1
**Related**: ADR-001, ADR-002, ADR-011, ADR-012, SPIKE-001, SPIKE-002, CONTRACT-002

## Purpose

This contract fixes the port through which the loop calls a model, the types that cross it, and the test doubles every user of the library can rely on. An adapter for a provider implements one method, `stream`; the loop and the session service know nothing else about models. The port is asynchronous (ADR-001), and the caller's identity is an explicit credential on the request (ADR-011).

## Scope and Boundaries

- In scope: the message, tool, request, and response types; the port; outage and cancellation behavior; concurrency and identity rules; the scripted, recording, and replay doubles; the cassette format.
- Out of scope: how an adapter reaches a provider (ADR-002 and its adapter), provider configuration, retry policy beyond "one layer", and the engine's use of the port (CONTRACT-002).
- Owning system or team: tether core. A changed signature or rule is a contract version change.

## Normative Surface

Signatures are Python 3.11. Payload types are frozen `pydantic` models with `extra="forbid"`; the port is a `typing.Protocol` class. MUST, MUST NOT, SHOULD, and MAY are used as defined in RFC 2119 (the IETF's key words for requirement levels).

### 1. Types and the port

```python
class ToolCall(BaseModel):  id: str; name: str; arguments: dict[str, JsonValue]
class AttachmentRef(BaseModel):  # names and sizes, never content
    id: str; filename: str; media_type: str; size_bytes: int; read: bool; withheld: bool = False
class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]; content: str
    attachments: tuple[AttachmentRef, ...] = (); tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None; is_error: bool = False
class ToolSpec(BaseModel):  name: str; description: str; input_schema: dict[str, JsonValue]
class RequestTags(BaseModel):  # rides on every request so a provider or gateway can tag it and a log can be reconciled
    session_id: str; principal: str; spec_id: str; spec_version: str
    turn_id: str; model_call_id: str
    purpose: Literal["answer", "summary", "correction"] = "answer"
    context: dict[str, tuple[str, ...]] = {}          # every scope value the session holds, by key
class ModelRequest(BaseModel):
    model: str                                        # a tier name; the adapter resolves it
    tags: RequestTags; system: str; messages: tuple[Message, ...]
    identity_mode: Literal["caller", "application", "anonymous"] = "caller"
    max_output_tokens: int                            # > 0; explicit profile output reservation (CONTRACT-004)
    tools: tuple[ToolSpec, ...] = (); effort: Literal["high", "medium", "low"] | None = None
    traceparent: str | None = None; timeout_seconds: float | None = None
    credential: Credential | None = Field(default=None, exclude=True, repr=False)   # the calling actor's credential (ADR-011)
    attempts: AttemptRecorder | None = Field(default=None, exclude=True, repr=False)  # CONTRACT-005; required for engine calls
    extra: dict[str, JsonValue] = {}                  # server-specific parameters sent unchanged (sampling, caching, grammar); the port gives them no meaning (ADR-012)
class Credential(Protocol):
    async def authorization(self) -> str | None: ...  # the value of an Authorization header, refreshed as needed
class TokenUsage(BaseModel):
    input_tokens: int; output_tokens: int               # >= 0; provider-reported totals
    cache_read_tokens: int | None = None; cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None                # optional subcounts; never added to totals again
class ModelResponse(BaseModel):
    content: str = ""; tool_calls: tuple[ToolCall, ...] = ()
    request_id: str | None = None; usage: TokenUsage | None = None
    receipt: ResponseReceipt | None = None             # header/request/invocation/body ids kept distinct (CONTRACT-005)
    finish_reason: Literal["stop", "tool_calls", "length", "filtered", "unknown"]
class TextDelta(BaseModel):  text: str
StreamItem = TextDelta | ModelResponse
class ModelUnavailable(Exception):  reason: str; request_id: str | None; receipt: ResponseReceipt | None
class ModelAttemptDenied(Exception):  reason: str  # local authorization refusal; no provider request

class ModelPort(Protocol):
    provider: str
    def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]: ...      # the one method an adapter implements

async def complete(port: ModelPort, request: ModelRequest) -> ModelResponse: ...  # helper: consumes `stream`, returns the final response
```

| Rule | Requirement |
|------|-------------|
| Stream shape | `stream` MUST yield zero or more `TextDelta`, then exactly one `ModelResponse` last. An adapter for a provider that does not stream yields one delta with the whole text, then the response. An outage MAY follow some deltas; the engine discards deltas yielded before an outage. |
| Outage | A provider/transport failure to produce a response MUST raise `ModelUnavailable`. Local `ModelAttemptDenied`, `StoreError`, `CommitUnknown`, `LeaseLost`, and cancellation propagate unchanged; they are not provider outages and MUST NOT be retried by the adapter. Its `reason` MUST be a short code (`http 503`, `timeout`, `unconfigured tier`, `incomplete stream`) and MUST NOT contain request or response content, a header value, or the text of an underlying exception. |
| Cancellation | A cancelled call MUST stop the underlying request and let the cancellation propagate; it MUST NOT be reported as an outage. |
| Content | Operational logs/errors never carry content. Explicit history capture is a separate content path: the engine/history adapter may persist credential-free requests/responses only through CONTRACT-005. Cassettes remain fabricated-only test fixtures. |
| Attachment metadata | AttachmentRef names and sizes describe application-owned artifacts. The selected text/function-tool integration renders every declared ref field as untrusted JSON text in the same message; it MUST NOT fetch or infer file contents. The original refs remain in authoritative history and ModelRequest. Native media/image/audio/binary transport requires a separately qualified profile. |
| Request id | `request_id` is a convenience identifier; `receipt` keeps provider response-header id, provider invocation id and completion-body id in separate fields with their provenance. A body id is not a proven inference-table join. Capture observed ids on stream headers and failures, not only successful terminal frames (CONTRACT-005). |
| Attempt correlation | The adapter reserves identity with `attempts.reserve()`, builds the final credential-free wire representation including its exact outbound attempt tags, then awaits `attempts.start(identity, request_digest=..., wire_request=..., codec=...)` before EVERY physical egress. It calls `observe` as soon as receipt metadata is available. Hidden SDK retries are disabled unless they can do this. Outbound required tags use the reserved attempt id; logical call and session/turn ids remain stable. An engine call without its recorder is refused before network I/O. |
| Concurrency | An adapter MUST be safe to call from many tasks at once. Caller mode uses only that request's credential; application mode uses only the explicitly configured application identity; anonymous mode sends no authorization. No mode inherits identity from another task. |
| Credential | Caller mode reads credential.authorization() once per physical attempt, never caches it across attempts, and refuses missing/empty/expired credentials before egress without application fallback. Application/anonymous modes are explicit request selections; passing a caller credential in either is identity_mode_conflict rather than silent credential substitution. Adapter construction validates the selected application credential mechanism. No credential is logged, persisted or included in an error reason. |
| Retries | An adapter MUST NOT retry in more than one layer; the layer that retries owns the policy. |
| Terminal outcome | The adapter normalizes only documented wire terminal reasons to `finish_reason`; missing/unrecognized reasons are `unknown`. A stream without a terminal response is `ModelUnavailable("incomplete_stream")`. Only `stop` without calls or `tool_calls` with complete valid calls permits continuation/final success. Truncated, filtered, unknown or structurally inconsistent outcomes MUST NOT dispatch a call or be labelled final success. |
| Wire integrity | Invalid JSON arguments, duplicate/ambiguous call ids in one reply, invalid names or incomplete calls MUST be rejected, never repaired into executable arguments. Provider-specific history formatting may add clearly synthetic non-executable separators; it MUST preserve the original log, invocation identity and effect status. |
| Bounds | `max_output_tokens` is mapped to the configured endpoint output-limit field. A conflicting reserved value in `extra` is `ModelUnavailable("parameter_conflict")`. Oversized/unmeasurable input is rejected by CONTRACT-004 before transport; the adapter caps response bytes and tool argument bytes by explicit settings before materialization. |
| Usage | Optional cache/reasoning counts retain missing values as `None`; they are subcounts, not extra billable totals. The engine distinguishes missing usage from zero and accounts for summary and answer purposes separately. |
| Retries and partial text | One layer owns a bounded retry policy with total deadline, attempt cap, finite Retry-After delay and jitter. Cancellation interrupts waits. Once a text delta has been emitted, the adapter MUST NOT silently restart another attempt; it reports an outage and the engine clears that attempt through CONTRACT-003. |
| Extra | An adapter MUST send non-reserved `extra` to the server unchanged and MUST NOT interpret it. A server that rejects a parameter reports an outage; the port does not translate between servers. A parameter the core or a contract requires MUST NOT live in `extra`. |
| Provider independence | Base/core packaging includes the selected pydantic-ai-slim Agent runtime and remains free of foundation-model provider SDKs under ADR-012. Explicit optional adapters may use native/direct/transitive SDKs behind the port, with dependency closure and local/custom model paths qualified. ADR-002/003 select Agent/model reuse; SPIKE-003/004 provide bounded evidence and SD-001 defines remaining qualification; SDK types do not enter core payloads. |
| Tier | `ModelRequest.model` is opaque to the port. An adapter MUST raise `ModelUnavailable("unconfigured tier")` for a tier it was not configured for. |

### 2. Test doubles (`tether.models.testing`)

| Double | Behavior |
|--------|----------|
| `ScriptedModel(script)` | Yields each item of `script` in order as a stream (an `Exception` item is raised; a `ModelResponse` item yields one delta of its text, then the response); records every request in `requests` (without the credential); raises `AssertionError` when the script is exhausted. |
| `RecordingModel(inner, writer, fabricated=True)` | Calls `inner`, appends one cassette record per call, and returns the result unchanged. `fabricated` MUST be passed as the literal `True`: the caller asserts the prompts hold no real data. A recorder MUST exclude `attempts` and all credential fields and MUST write `RequestTags.principal` as the fixed value `recorded` and MUST NOT write the credential or anything the port does not carry. |
| `ReplayModel(cassette, mode)` | `mode="sequence"` returns records in order and fails if the request differs from the recorded one. `mode="keyed"` returns the record whose key matches, in any order. An unmatched request raises `AssertionError` naming the first differing field, never the content. |

### 3. Cassette, version 1

JSON Lines, one object per line. The first line is `{"cassette": 1, "provider": "<name>"}`. Each later line is `{"key": "<sha256 hex>", "request": <ModelRequest without traceparent and timeout_seconds>, "stream": ["<delta text>", ...], "outcome": {"response": <ModelResponse>} | {"unavailable": {"reason": "...", "request_id": "...", "receipt": <ResponseReceipt or null>}}}`. The key is the SHA-256 of the canonical JSON (sorted keys, no whitespace) of the request with `traceparent`, `timeout_seconds`, `credential`, `attempts`, `tags.session_id`, `tags.turn_id`, `tags.model_call_id`, and `tags.principal` removed. Attempt-recorder callbacks/credentials are never serialized; replay supplies a deterministic recorder and preserves purpose/order. Failure outcomes preserve an observed `ResponseReceipt` when present; an absent receipt remains absent. Replay supplies that same provenance to the attempt recorder and the raised `ModelUnavailable`. A reader MUST refuse a cassette whose version it does not know.

### 4. Adapter execution limits

An adapter is constructed with a `ModelExecutionLimits` value: `max_response_bytes` and `max_tool_argument_bytes` (positive integers over decoded UTF-8 payload bytes), `max_attempts` (positive integer including the first attempt), `request_timeout_seconds` (positive finite total deadline including retries/backoff) and `max_retry_delay_seconds` (positive finite cap). These settings are explicit; invalid/non-finite Retry-After values are ignored and bounded jittered policy applies. Waiting beyond the remaining total deadline ends `timeout`; it does not start another attempt. An endpoint that cannot enforce the configured output field refuses configuration, rather than silently dropping it.

### Attachment metadata text codec

`tether-attachment-metadata-v1` renders a message with refs as its original content followed by
`\n\n<attachment-metadata codec="tether-attachment-metadata-v1">\n`, a canonical UTF-8 JSON
array of refs in original order, then `\n</attachment-metadata>`. Every object carries `id`,
`filename`, `media_type`, `size_bytes`, `read`, and `withheld`; duplicate refs are preserved.
Canonical JSON sorts object keys and escapes control characters. Ref fields and marker-looking
ordinary text remain untrusted data. The flags confer no access or execution permission.
Messages without refs retain their content. Roles, call IDs, arguments and error state are unchanged.

The SDK projection carries this text with an empty attachments tuple. Decoding SDK messages alone
returns that derived text; it MUST NOT parse markers into refs. Engine and the one-step Agent helper
verify the complete ordered projection against their currently authorized original source messages
before restoring originals for preparation. Equality includes system text. Duplicate-looking
messages are restored by their known positions only after complete equality; no content matching,
SDK metadata or checkpoint grants source authority. Native adapters render the restored refs once.
Returned SDK conversations remain derivatives and contain rendered metadata text.

## Precedence and Compatibility

The initial pre-release v1 is revised in place; there is no released contract to preserve or compatibility wrapper. Cassettes use the final v1 shapes, including output limits, purpose and terminal reasons. Existing experimental fixtures must be explicitly regenerated rather than guessed.

- Versioning: one integer, `MODEL_PORT_VERSION`, exported by the package.
- Ordering or precedence: this contract governs the port; CONTRACT-002 governs how the engine uses it.
- Backward-compatibility rules: adding an optional field with a default is compatible. Changing a signature, a rule, or the cassette key is a new version, and a reader keeps reading older cassette versions it lists.
- Deprecation rules: a removed item is announced one version ahead and kept, with a warning, for that version.

## Error Semantics

Malformed wire payloads use `invalid_response`; byte/argument caps use `response_too_large`; provider window rejection uses `context_window_exceeded`; missing terminal frames use `incomplete_stream`. Reasons never contain response bodies. These outcomes are outages, never executable repaired calls.

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| Local attempt authorization denies or errors | `ModelAttemptDenied` with a metadata-only reason | No | No egress; engine ends `model_denied` (summary path: `context_denied`) |
| Attempt journal rejects, loses lease, or cannot establish commit | Original `StoreError`, `LeaseLost`, or `CommitUnknown` | No | No egress; journal recovery determines acceptance by the original key |
| The provider is unreachable or refuses | `ModelUnavailable` with a short reason and the provider's request id when it sent one | The caller decides | The record names the failed call |
| A tier the adapter was not configured for | `ModelUnavailable("unconfigured tier")` | No | Fix the configuration |
| The call is cancelled | Cancellation propagates | No | None |
| A request carries a credential the adapter cannot use | `ModelUnavailable` with a short reason such as `http 401`; the credential is not echoed | The caller decides | The caller refreshes or reauthenticates |
| `ScriptedModel` runs out | `AssertionError` | No | Extend the script |
| `ReplayModel` mismatch | `AssertionError` naming the first differing field | No | Re-record on fabricated prompts, or fix the test |
| `RecordingModel` without `fabricated=True` | `TypeError` at construction | No | Pass the literal, asserting fabricated prompts |

## Examples

```python
model = ScriptedModel([
    ModelResponse(finish_reason="tool_calls", tool_calls=(ToolCall(id="c1", name="lookup", arguments={"q": "sample"}),)),
    ModelResponse(content="Found it.", finish_reason="stop"),
])
```

```text
{"cassette": 1, "provider": "scripted"}
{"key": "9f2c...e1", "request": {"model": "balanced", "tags": {"spec_id": "assistant", "spec_version": "1.0.0", "context": {}}, "system": "...", "messages": [...], "tools": [...], "max_output_tokens": 256}, "stream": [], "outcome": {"response": {"content": "", "finish_reason": "tool_calls", "tool_calls": [{"id": "c1", "name": "lookup", "arguments": {"q": "sample"}}]}}}
```

## Non-Normative Notes

Conformance includes prepared-before-egress correlation, unique physical attempt ids, separate header/body/request/invocation receipts on success/error, faithful history capture without credentials, and complete payload/output limits, finish-reason normalization, tool-argument rejection, cache/reasoning usage, bounded Retry-After values, cancellation during backoff, and partial-stream failure with no hidden second attempt. CONTRACT-004 owns budget estimation and summary requests.

- The required protocol integration supports the declared OpenAI-compatible chat-completions subset, which local and self-hosted servers implement; the name denotes the wire protocol, not an SDK or a service. Which servers it works with is established by the adapter's conformance matrix, not by this contract.
- A model port is deliberately small: the loop needs a stream that ends in a response, and a way to name the failure. A single method keeps every adapter and every double to one behavior to get right; callers that want a whole response use `complete`. Provider features beyond that stay in the adapter.
- `RequestTags` exists so a provider or gateway that attributes calls by header can do so, and so a provider's log can be reconciled with the session record. How an adapter renders the tags (for example into a `User-Agent`) is the adapter's concern.


Model-port version 2 adds typed per-physical-attempt usage to the excluded AttemptRecorder.observe
callback (CONTRACT-005). Native adapters and scripted/recording/replay doubles MUST forward known
TokenUsage without parsing it in the engine. This changes callback implementations, not the stream
shape or cassette-v1 response format. An old cassette lacking retry observations does not establish
physical-retry audit equivalence; complete cassette/attempt qualification remains a separate gate.
