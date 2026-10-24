"""Linear reconstruction preserves independent totals and staging isolation."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tether.stores.projections import create_index
from tether.types import TokenUsage
from tether.types.execution import NewEntry
from tether.types.storage import IntegrityError, SessionInit

AT = datetime(2026, 1, 1, tzinfo=UTC)


def usage(identity):
    return TokenUsage(
        input_tokens=identity,
        output_tokens=identity + 1,
        cache_read_tokens=0 if identity % 2 == 0 else None,
        cache_write_tokens=identity if identity > 2 else None,
        reasoning_tokens=identity if identity % 3 == 0 else None,
    )


def attempt(identity, known, key):
    return NewEntry(
        kind="model_attempt",
        key=key,
        facts={
            "identity": {"attempt_id": str(identity), "model_call_id": "logical"},
            "status": "failed" if identity % 2 else "ok",
            "usage": usage(identity).model_dump(mode="json") if known else None,
        },
    )


@given(st.lists(st.tuples(st.integers(min_value=0, max_value=5), st.booleans()), max_size=30))
def test_usage_deltas_match_independent_per_attempt_totals(sequence):
    index = create_index("session", "owner", SessionInit(), AT)
    expected = {}
    for position, (identity, known) in enumerate(sequence):
        index.append((attempt(identity, known, str(position)),), AT)
        if known or identity not in expected:
            expected[identity] = usage(identity) if known else None
        values = [value for value in expected.values() if value is not None]
        fields = ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
        optional = {}
        for field in fields:
            reported = [
                getattr(value, field) for value in values if getattr(value, field) is not None
            ]
            optional[field] = sum(reported) if reported else None
        assert index.summary.usage == TokenUsage(
            input_tokens=sum(value.input_tokens for value in values),
            output_tokens=sum(value.output_tokens for value in values),
            **optional,
        )
        assert index.missing_usage == sum(value is None for value in expected.values())
        assert index.summary.usage_complete == all(value is not None for value in expected.values())


def test_failed_staged_usage_cannot_mutate_accepted_group_lists_or_counters():
    index = create_index("session", "owner", SessionInit(), AT)
    index.append((attempt(1, True, "original"),), AT)
    original_usage = index.summary.usage
    original_group = tuple(index.lookups[("model_call", "logical")])
    staged = index.clone()
    changed = attempt(1, True, "conflicting").model_dump(mode="json")
    changed["facts"]["usage"]["input_tokens"] = 999
    with pytest.raises(IntegrityError, match="usage_changed"):
        staged.append((NewEntry.model_validate(changed),), AT)
    assert tuple(index.lookups[("model_call", "logical")]) == original_group
    assert index.summary.usage == original_usage
    assert len(index.entries) == 1
    assert index.missing_usage == 0
