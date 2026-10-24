"""Closed context metadata contains no original, summary, marker or tool content."""

from tether.types import JsonValue
from tether.types.context import ContextUse
from tether.types.execution import Entry


def context_detail(entry: Entry) -> dict[str, JsonValue]:
    if entry.kind != "context_use":
        value = entry.facts.get("preparation_ordinal")
        return {"preparation_ordinal": value} if type(value) is int and value >= 0 else {}
    use = ContextUse.model_validate(entry.facts)
    detail: dict[str, JsonValue] = {
        "preparation_ordinal": use.preparation_ordinal,
        "through_index": use.through_index,
        "source_view_digest": use.source_view_digest,
        "request_digest": use.request_digest,
        "config_digest": use.config_digest,
        "parts_count": len(use.parts),
        "omission_parts": sum(p.kind == "omit" for p in use.parts),
        "estimate_tokens": use.estimate.tokens,
        "estimate_quality": use.estimate.quality,
        "output_tokens": use.output_tokens,
        "safety_margin_tokens": use.safety_margin_tokens,
    }
    for field, value in (
        ("model_call_id", use.model_call_id),
        ("profile_id", use.profile_id),
        ("profile_revision", use.profile_revision),
        ("estimate_method", use.estimate.method),
        ("estimate_revision", use.estimate.revision),
    ):
        if len(value) <= 256:
            detail[field] = value
    return detail
