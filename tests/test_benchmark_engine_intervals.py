from __future__ import annotations

import pytest
from benchmark_engine_intervals import (
    TimedInterval,
    duration_ns,
    exclusive_engine_duration_ns,
    subtract_intervals,
    union_intervals,
)


def test_union_merges_overlapping_and_touching_spans_once():
    assert union_intervals(
        (
            TimedInterval(10, 20),
            TimedInterval(15, 25),
            TimedInterval(25, 30),
            TimedInterval(1, 4),
        )
    ) == (TimedInterval(1, 4), TimedInterval(10, 30))


def test_subtraction_handles_nested_partial_and_duplicate_masks():
    assert subtract_intervals(
        (TimedInterval(10, 40),),
        (TimedInterval(15, 20), TimedInterval(18, 25), TimedInterval(30, 35)),
    ) == (TimedInterval(10, 15), TimedInterval(25, 30), TimedInterval(35, 40))


def test_engine_boundary_keeps_recorder_engine_work_but_excludes_nested_storage():
    step = TimedInterval(0, 100)
    model = (TimedInterval(10, 90),)
    callbacks = (TimedInterval(30, 45), TimedInterval(60, 75))
    storage = (TimedInterval(5, 10), TimedInterval(35, 40), TimedInterval(70, 80))

    # Model-only work is [10,30), [45,60), [75,90). Storage is excluded
    # everywhere, including the portions nested inside attempt callbacks.
    assert exclusive_engine_duration_ns(step, model, callbacks, storage) == 35


@pytest.mark.parametrize(
    ("start", "end"),
    [(True, 1), (2, 1), (-1, 2), (0.5, 1)],
    ids=("boolean", "reversed", "negative", "fractional"),
)
def test_invalid_intervals_refuse(start, end):
    with pytest.raises(ValueError, match="benchmark_interval_invalid"):
        TimedInterval(start, end)


def test_duration_unions_nested_intervals():
    assert duration_ns((TimedInterval(0, 10), TimedInterval(5, 15))) == 15
