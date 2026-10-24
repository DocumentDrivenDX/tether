"""Per-attempt usage identities and deltas without transcript-sized state."""

from collections.abc import Iterable
from dataclasses import dataclass

from tether.types import TokenUsage
from tether.types.execution import Entry
from tether.types.storage import IntegrityError


def usage_value(entry: Entry) -> tuple[str, TokenUsage | None]:
    identity = entry.facts.get("identity")
    attempts = entry.facts.get("attempt_ids")
    if entry.kind == "model_attempt" and isinstance(identity, dict):
        key = "attempt:" + str(identity.get("attempt_id"))
    elif isinstance(attempts, list) and attempts:
        key = "attempt:" + str(attempts[-1])
    else:
        key = "logical:" + str(entry.facts.get("model_call_id", entry.index))
    raw = entry.facts.get("usage")
    return key, TokenUsage.model_validate(raw) if raw is not None else None


@dataclass(frozen=True)
class UsageChange:
    value: TokenUsage | None
    added: TokenUsage
    missing_delta: int


def usage_change(
    previous: TokenUsage | None, value: TokenUsage | None, *, exists: bool
) -> UsageChange:
    if previous is not None:
        if value is not None and previous != value:
            raise IntegrityError("usage_changed")
        return UsageChange(previous, TokenUsage(input_tokens=0, output_tokens=0), 0)
    missing = (-1 if exists else 0) if value is not None else (0 if exists else 1)
    return UsageChange(value, value or TokenUsage(input_tokens=0, output_tokens=0), missing)


def _optional(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


def add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_read_tokens=_optional(left.cache_read_tokens, right.cache_read_tokens),
        cache_write_tokens=_optional(left.cache_write_tokens, right.cache_write_tokens),
        reasoning_tokens=_optional(left.reasoning_tokens, right.reasoning_tokens),
    )


def sum_usage(values: Iterable[TokenUsage]) -> TokenUsage:
    result = TokenUsage(input_tokens=0, output_tokens=0)
    for value in values:
        result = add_usage(result, value)
    return result
