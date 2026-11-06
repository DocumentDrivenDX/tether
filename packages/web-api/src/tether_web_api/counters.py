"""Field-directed decimal-string conversion for v1 counters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast


class CounterValueError(ValueError):
    """A counter did not satisfy the lossless HTTP wire representation."""


def _resolve(schema: Mapping[str, Any], definitions: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return definitions[ref.rsplit("/", 1)[1]]
    return schema


# This ordered matcher handles nullable, discriminated, and generic unions in JSON Schema.
def _choice(schema: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
    raw_variants = schema.get("oneOf", schema.get("anyOf"))
    if not isinstance(raw_variants, list):
        return schema
    variants = cast(list[Mapping[str, Any]], raw_variants)
    optional = _null_variant(variants, value)
    if optional is not None:
        return optional
    discriminator_value = schema.get("discriminator", {})
    discriminator_schema = cast(Mapping[str, Any], discriminator_value)
    discriminator = discriminator_schema.get("propertyName")
    if isinstance(discriminator, str) and isinstance(value, Mapping):
        mapped = _tagged_variant(schema, discriminator, cast(Mapping[str, Any], value))
        if mapped is not None:
            return mapped
    counter = next((item for item in variants if item.get("x-tether-counter")), None)
    return counter or _matching_variant(variants, value) or schema


def _null_variant(variants: list[Mapping[str, Any]], value: Any) -> Mapping[str, Any] | None:
    if value is not None:
        return None
    return next(
        (item for item in variants if item.get("type") == "null"),
        None,
    )


def _tagged_variant(
    schema: Mapping[str, Any], discriminator: str, value: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    discriminator_info = cast(Mapping[str, Any], schema.get("discriminator", {}))
    mapping = cast(Mapping[str, Any], discriminator_info.get("mapping", {}))
    tag = value.get(discriminator)
    if not isinstance(tag, str):
        return None
    ref = mapping.get(tag)
    return {"$ref": ref} if isinstance(ref, str) else None


def _matching_variant(variants: list[Mapping[str, Any]], value: Any) -> Mapping[str, Any] | None:
    for variant in variants:
        kind = variant.get("type")
        matches = (
            "$ref" in variant
            or (kind == "object" and isinstance(value, Mapping))
            or (kind == "array" and isinstance(value, list))
            or (kind == "string" and isinstance(value, str))
        )
        if matches:
            return variant
    return None


def convert_counters(
    value: Any, schema_name: str, registry: Mapping[str, Any], *, to_wire: bool
) -> Any:
    """Convert only schema-marked counter leaves, preserving application JSON values."""
    definitions = cast(Mapping[str, Any], registry.get("$defs", {}))
    return _convert(value, {"$ref": f"#/$defs/{schema_name}"}, definitions, to_wire)


def _convert(
    value: Any, schema: Mapping[str, Any], definitions: Mapping[str, Any], to_wire: bool
) -> Any:
    schema = _resolve(schema, definitions)
    schema = _choice(schema, value)
    schema = _resolve(schema, definitions)
    if schema.get("x-tether-counter"):
        if to_wire:
            if type(value) is not int or (
                value < 0 and not (value == -1 and schema.get("x-tether-counter-signed"))
            ):
                raise CounterValueError("counter_out_of_range")
            return str(value)
        signed = schema.get("x-tether-counter-signed", False)
        digits = value[1:] if signed and isinstance(value, str) and value.startswith("-") else value
        if not isinstance(value, str) or not value.isascii() or not digits.isdecimal():
            raise CounterValueError("counter_must_be_decimal_string")
        if len(digits) > 1 and digits[0] == "0":
            raise CounterValueError("counter_must_be_canonical_decimal")
        result = int(value)
        if result < 0 and not (signed and result == -1):
            raise CounterValueError("counter_out_of_range")
        return result
    kind = schema.get("type")
    if kind == "object" and isinstance(value, Mapping):
        props = cast(Mapping[str, Any], schema.get("properties", {}))
        source = cast(Mapping[str, Any], value)
        return {
            key: _convert(item, props[key], definitions, to_wire) if key in props else item
            for key, item in source.items()
        }
    if kind == "array" and isinstance(value, list):
        items = cast(Mapping[str, Any], schema.get("items", {}))
        values = cast(list[Any], value)
        return [_convert(item, items, definitions, to_wire) for item in values]
    return value
