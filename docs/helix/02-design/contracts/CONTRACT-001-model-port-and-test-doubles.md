---
ddx:
  id: CONTRACT-001
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: ADR-001
      kind: informed_by
    - id: ADR-002
      kind: informed_by
    - id: SPIKE-001
      kind: informed_by
    - id: architecture
      kind: informed_by
---

# CONTRACT-001: Model Port and Test Doubles

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-001
**Type**: library (Python port)
**Version**: v1
**Related**: ADR-001, ADR-002, SPIKE-001, CONTRACT-002

## Purpose

This contract fixes the port through which the loop calls a model, the types that cross it, and the test doubles every user of the library can rely on. An adapter for a provider implements the port; the loop and the session service know nothing else about models. The port is asynchronous (ADR-001).

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
    context: dict[str, tuple[str, ...]] = {}          # every scope value the session holds, by key
class ModelRequest(BaseModel):
    model: str                                        # a tier name; the adapter resolves it
    tags: RequestTags; system: str; messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = (); effort: Literal["high", "medium", "low"] | None = None
    traceparent: str | None = None; timeout_seconds: float | None = None
class TokenUsage(BaseModel):  input_tokens: int; output_tokens: int          # both >= 0
class ModelResponse(BaseModel):
    content: str = ""; tool_calls: tuple[ToolCall, ...] = ()
    request_id: str | None = None; usage: TokenUsage | None = None
class TextDelta(BaseModel):  text: str
StreamItem = TextDelta | ModelResponse
class ModelUnavailable(Exception):  reason: str; request_id: str | None

class ModelPort(Protocol):
    provider: str
    async def complete(self, request: ModelRequest) -> ModelResponse: ...
    def complete_stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]: ...
```

| Rule | Requirement |
|------|-------------|
| Stream shape | `complete_stream` MUST yield zero or more `TextDelta`, then exactly one `ModelResponse` last. An outage MAY follow some deltas; the engine discards deltas yielded before an outage. |
| Outage | Any failure to produce a response MUST raise `ModelUnavailable`. Its `reason` MUST be a short code (`http 503`, `timeout`, `unconfigured tier`, `incomplete stream`) and MUST NOT contain request or response content, a header value, or the text of an underlying exception. |
| Cancellation | A cancelled call MUST stop the underlying request and let the cancellation propagate; it MUST NOT be reported as an outage. |
| Content | An adapter MUST NOT log or return prompt, tool, or response content outside the response it returns. |
| Request id | `request_id` SHOULD be the provider's id for the call, the key its own log is read by; `None` when the provider gives none. |
| Concurrency | An adapter MUST be safe to call from many tasks at once and MUST carry each call's own identity (the caller's credential) with that call. |
| Retries | An adapter MUST NOT retry in more than one layer; the layer that retries owns the policy. |
| Tier | `ModelRequest.model` is opaque to the port. An adapter MUST raise `ModelUnavailable("unconfigured tier")` for a tier it was not configured for. |

### 2. Test doubles (`tether.models.testing`)

| Double | Behavior |
|--------|----------|
| `ScriptedModel(script)` | Returns each item of `script` in order (an `Exception` item is raised); records every request in `requests`; raises `AssertionError` when the script is exhausted. |
| `RecordingModel(inner, writer, fabricated=True)` | Calls `inner`, appends one cassette record per call, and returns the result unchanged. `fabricated` MUST be passed as the literal `True`: the caller asserts the prompts hold no real data. A recorder MUST write `RequestTags.principal` as the fixed value `recorded` and MUST NOT write anything the port does not carry (the port carries no header and no credential). |
| `ReplayModel(cassette, mode)` | `mode="sequence"` returns records in order and fails if the request differs from the recorded one. `mode="keyed"` returns the record whose key matches, in any order. An unmatched request raises `AssertionError` naming the first differing field, never the content. |

### 3. Cassette, version 1

JSON Lines, one object per line. The first line is `{"cassette": 1, "provider": "<name>"}`. Each later line is `{"key": "<sha256 hex>", "request": <ModelRequest without traceparent and timeout_seconds>, "stream": ["<delta text>", ...], "outcome": {"response": <ModelResponse>} | {"unavailable": {"reason": "...", "request_id": "..."}}}`. The key is the SHA-256 of the canonical JSON (sorted keys, no whitespace) of the request with `traceparent`, `timeout_seconds`, `tags.session_id`, and `tags.principal` removed. A reader MUST refuse a cassette whose version it does not know.

## Precedence and Compatibility

- Versioning: one integer, `MODEL_PORT_VERSION`, exported by the package.
- Ordering or precedence: this contract governs the port; CONTRACT-002 governs how the engine uses it.
- Backward-compatibility rules: adding an optional field with a default is compatible. Changing a signature, a rule, or the cassette key is a new version, and a reader keeps reading older cassette versions it lists.
- Deprecation rules: a removed item is announced one version ahead and kept, with a warning, for that version.

## Error Semantics

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| The provider is unreachable or refuses | `ModelUnavailable` with a short reason and the provider's request id when it sent one | The caller decides | The record names the failed call |
| A tier the adapter was not configured for | `ModelUnavailable("unconfigured tier")` | No | Fix the configuration |
| The call is cancelled | Cancellation propagates | No | None |
| `ScriptedModel` runs out | `AssertionError` | No | Extend the script |
| `ReplayModel` mismatch | `AssertionError` naming the first differing field | No | Re-record on fabricated prompts, or fix the test |
| `RecordingModel` without `fabricated=True` | `TypeError` at construction | No | Pass the literal, asserting fabricated prompts |

## Examples

```python
model = ScriptedModel([
    ModelResponse(tool_calls=(ToolCall(id="c1", name="lookup", arguments={"q": "sample"}),)),
    ModelResponse(content="Found it."),
])
```

```text
{"cassette": 1, "provider": "scripted"}
{"key": "9f2c...e1", "request": {"model": "balanced", "tags": {"spec_id": "assistant", "spec_version": "1.0.0", "context": {}}, "system": "...", "messages": [...], "tools": [...]}, "stream": [], "outcome": {"response": {"content": "", "tool_calls": [{"id": "c1", "name": "lookup", "arguments": {"q": "sample"}}]}}}
```

## Non-Normative Notes

- A model port is deliberately small: the loop needs a completed response, a stream, and a way to name the failure. Provider features beyond that stay in the adapter.
- `RequestTags` exists so a provider or gateway that attributes calls by header can do so, and so a provider's log can be reconciled with the session record. How an adapter renders the tags (for example into a `User-Agent`) is the adapter's concern.
