"""Pinned definitions and one-pass Pydantic argument normalization."""

from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, JsonValue

from tether.types import ToolSpec
from tether.types.canonical import canonical, digest
from tether.types.execution import ExecutionDenied, Tool

ValidatedArgs = BaseModel


@dataclass(frozen=True)
class Normalized:
    value: BaseModel
    arguments: dict[str, JsonValue]
    sha256: str


def definition(tool: Tool) -> dict[str, JsonValue]:
    if (
        not tool.version
        or tool.effect not in {"read_only", "effectful"}
        or tool.replay not in {"safe", "unsafe"}
    ):
        raise ValueError("invalid_tool_definition")
    return {
        "name": tool.name,
        "version": tool.version,
        "description": tool.description,
        "schema": tool.input_model.model_json_schema(),
        "requires": [str(v) for v in sorted(tool.requires)],
        "traits": tool.traits,
        "effect": tool.effect,
        "replay": tool.replay,
        "always_ask": tool.always_ask,
        "definition_hash": tool.definition_hash,
    }


def tool_spec(tool: Tool) -> ToolSpec:
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_model.model_json_schema(),
    )


def normalize(tool: Tool, raw: dict[str, JsonValue], max_bytes: int) -> Normalized:
    try:
        if len(canonical(raw)) > max_bytes:
            raise ValueError("argument_limit")
        value = tool.input_model.model_validate_json(canonical(raw))
        plain: dict[str, Any] = value.model_dump(mode="python", round_trip=True)
        # Non-JSON executable values need an explicit lossless codec; none is selected yet.
        encoded = canonical(plain)
        if encoded != canonical(executable_json(value)):
            raise ValueError("lossy_argument_serialization")
        if len(encoded) > max_bytes:
            raise ValueError("argument_limit")
        return Normalized(value=value, arguments=plain, sha256=digest(plain))
    except (ValueError, TypeError, OverflowError):
        raise ExecutionDenied("invalid_arguments") from None


def executable_json(value: object) -> JsonValue:
    """Read validated field values without serializers that could change approval meaning."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseModel):
        return {key: executable_json(item) for key, item in value}
    if isinstance(value, dict):
        items = cast(dict[object, object], value)
        if not all(isinstance(key, str) for key in items):
            raise ValueError("non_json_argument")
        return {cast(str, key): executable_json(item) for key, item in items.items()}
    if isinstance(value, list):
        return [executable_json(item) for item in cast(list[object], value)]
    raise ValueError("non_json_argument")
