"""Per-attempt usage identities and deltas without transcript-sized state."""

from collections.abc import Iterable
from dataclasses import dataclass

from tether.types import AttemptIdentity, TokenUsage
from tether.types.execution import Entry, UsageSoFar
from tether.types.storage import IntegrityError


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise IntegrityError("usage_identity_invalid")
    return value


def model_call_id(value: object, index: int) -> str:
    return "entry:" + str(index) if value is None else _identifier(value)


def usage_value(entry: Entry) -> tuple[str, TokenUsage | None]:
    identity = entry.facts.get("identity")
    if entry.kind == "model_receipt" and "usage" in entry.facts:
        try:
            AttemptIdentity.model_validate(identity)
        except ValueError:
            raise IntegrityError("usage_identity_invalid") from None
    attempts = entry.facts.get("attempt_ids")
    if entry.kind in {"model_attempt", "model_receipt"} and isinstance(identity, dict):
        key = "attempt:" + _identifier(identity.get("attempt_id"))
    elif isinstance(attempts, list) and attempts:
        key = "attempt:" + _identifier(attempts[-1])
    else:
        key = "logical:" + model_call_id(entry.facts.get("model_call_id"), entry.index)
    raw = entry.facts.get("usage")
    return key, TokenUsage.model_validate(raw) if raw is not None else None


def usage_observations(entry: Entry) -> tuple[tuple[str, TokenUsage | None], ...]:
    observed = (usage_value(entry),)
    if entry.kind == "model_call" and entry.facts.get("attempts_complete") is False:
        return (*observed, ("unobserved:" + observed[0][0], None))
    return observed


@dataclass(frozen=True)
class UsageChange:
    value: TokenUsage | None
    added: TokenUsage
    missing_delta: int


def usage_change(
    previous: TokenUsage | None, value: TokenUsage | None, *, exists: bool
) -> UsageChange:
    if previous is not None:
        merged, added = _enrich(previous, value)
        return UsageChange(merged, added, 0)
    missing = (-1 if exists else 0) if value is not None else (0 if exists else 1)
    return UsageChange(value, value or TokenUsage(input_tokens=0, output_tokens=0), missing)


def _enrich(previous: TokenUsage, value: TokenUsage | None) -> tuple[TokenUsage, TokenUsage]:
    merged = previous.model_dump()
    added = TokenUsage(input_tokens=0, output_tokens=0).model_dump()
    if value is not None:
        for field, new in value.model_dump().items():
            old = merged[field]
            if old is not None and new is not None and old != new:
                raise IntegrityError("usage_changed")
            if old is None and new is not None:
                merged[field], added[field] = new, new
    return TokenUsage.model_validate(merged), TokenUsage.model_validate(added)


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


class UsageLedger:
    """Incremental accepted-fact accounting shared by policy and reconstruction."""

    def __init__(self) -> None:
        self.values: dict[str, TokenUsage | None] = {}
        self.purposes: dict[str, str] = {}
        self.calls: dict[str, str] = {}
        self.bindings: dict[str, str] = {}
        self.identities: dict[str, object] = {}
        self.seen: set[int] = set()
        self.total = TokenUsage(input_tokens=0, output_tokens=0)
        self.missing = 0
        self.totals: dict[str, TokenUsage] = {}
        self.missing_by_purpose: dict[str, int] = {}
        self.counts: dict[str, int] = {}
        self.call_totals: dict[str, TokenUsage] = {}
        self.missing_by_call: dict[str, int] = {}

    def apply(self, entry: Entry) -> None:
        if entry.index in self.seen or entry.kind not in USAGE_KINDS:
            return
        identity = entry.facts.get("identity")
        metadata = identity if isinstance(identity, dict) else entry.facts
        purpose = str(metadata.get("purpose", "answer"))
        call = model_call_id(metadata.get("model_call_id"), entry.index)
        if self.calls.get(call, purpose) != purpose:
            raise IntegrityError("usage_purpose_changed")
        observations = usage_observations(entry)
        for key, _ in observations:
            if isinstance(identity, dict) and self.identities.get(key, identity) != identity:
                raise IntegrityError("usage_identity_changed")
            if self.bindings.get(key, call) != call:
                raise IntegrityError("usage_identity_changed")
        for key, value in observations:
            self.bindings[key] = call
            if isinstance(identity, dict):
                self.identities[key] = dict(identity)
            self._observe(key, value, purpose, call)
        if call not in self.calls:
            self.counts[purpose] = self.counts.get(purpose, 0) + 1
        self.calls[call] = purpose
        self.seen.add(entry.index)

    def _observe(self, key: str, value: TokenUsage | None, purpose: str, call: str) -> None:
        if self.purposes.get(key, purpose) != purpose:
            raise IntegrityError("usage_purpose_changed")
        change = usage_change(self.values.get(key), value, exists=key in self.values)
        self.values[key], self.purposes[key] = change.value, purpose
        self.total = add_usage(self.total, change.added)
        self.missing += change.missing_delta
        zero = TokenUsage(input_tokens=0, output_tokens=0)
        self.totals[purpose] = add_usage(self.totals.get(purpose, zero), change.added)
        self.call_totals[call] = add_usage(self.call_totals.get(call, zero), change.added)
        self.missing_by_call[call] = self.missing_by_call.get(call, 0) + change.missing_delta
        self.missing_by_purpose[purpose] = (
            self.missing_by_purpose.get(purpose, 0) + change.missing_delta
        )

    def call_usage(self, call: str) -> tuple[TokenUsage | None, bool]:
        value = self.call_totals.get(call)
        return (
            value.model_copy(deep=True) if value is not None else None,
            call in self.calls and self.missing_by_call.get(call, 0) == 0,
        )

    def snapshot(self) -> UsageSoFar:
        return UsageSoFar(
            steps=self.counts.get("answer", 0),
            model_calls=len(self.calls),
            summary_calls=self.counts.get("summary", 0),
            input_tokens=self.total.input_tokens,
            output_tokens=self.total.output_tokens,
            usage_complete=self.missing == 0,
            by_purpose={p: v.model_copy(deep=True) for p, v in self.totals.items()},
            missing_by_purpose=dict(self.missing_by_purpose),
        )


USAGE_KINDS = frozenset({"model_attempt", "model_receipt", "model_call"})
