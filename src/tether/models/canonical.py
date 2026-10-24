"""Versioned canonical JSON and credential-free request snapshots."""

from pydantic import JsonValue

from tether.types import ModelRequest
from tether.types.canonical import canonical as canonical
from tether.types.canonical import digest as digest


def snapshot(request: ModelRequest) -> ModelRequest:
    return ModelRequest.model_validate(request.model_dump(mode="json"))


def cassette_request(request: ModelRequest) -> dict[str, JsonValue]:
    value = request.model_dump(mode="json", exclude={"traceparent", "timeout_seconds"})
    value["tags"]["principal"] = "recorded"
    return value


def matching_request(request: ModelRequest) -> dict[str, JsonValue]:
    value = cassette_request(request)
    tags = value["tags"]
    if not isinstance(tags, dict):
        raise ValueError("invalid_request_tags")
    for name in ("session_id", "turn_id", "model_call_id", "principal"):
        tags.pop(name, None)
    return value


def copy_request(request: ModelRequest) -> ModelRequest:
    return snapshot(request).model_copy(
        update={"credential": request.credential, "attempts": request.attempts}
    )
