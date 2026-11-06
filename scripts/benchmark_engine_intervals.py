"""Pure interval accounting for the qualified public engine timing boundary."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, order=True, slots=True)
class TimedInterval:
    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if (
            type(self.start_ns) is not int
            or type(self.end_ns) is not int
            or self.start_ns < 0
            or self.end_ns < self.start_ns
        ):
            raise ValueError("benchmark_interval_invalid")


def union_intervals(intervals: Iterable[TimedInterval]) -> tuple[TimedInterval, ...]:
    values = tuple(intervals)
    if any(not isinstance(interval, TimedInterval) for interval in values):
        raise ValueError("benchmark_interval_invalid")
    ordered = sorted(values)
    if not ordered:
        return ()
    merged: list[TimedInterval] = []
    for interval in ordered:
        if merged and interval.start_ns <= merged[-1].end_ns:
            previous = merged[-1]
            merged[-1] = TimedInterval(previous.start_ns, max(previous.end_ns, interval.end_ns))
        else:
            merged.append(interval)
    return tuple(merged)


def subtract_intervals(
    intervals: Iterable[TimedInterval], excluded: Iterable[TimedInterval]
) -> tuple[TimedInterval, ...]:
    """Return the interval union after subtracting the union of excluded spans."""
    base = union_intervals(intervals)
    masks = union_intervals(excluded)
    remaining: list[TimedInterval] = []
    for interval in base:
        cursor = interval.start_ns
        for mask in masks:
            if mask.end_ns <= cursor:
                continue
            if mask.start_ns >= interval.end_ns:
                break
            if mask.start_ns > cursor:
                remaining.append(TimedInterval(cursor, min(mask.start_ns, interval.end_ns)))
            cursor = max(cursor, mask.end_ns)
            if cursor >= interval.end_ns:
                break
        if cursor < interval.end_ns:
            remaining.append(TimedInterval(cursor, interval.end_ns))
    return tuple(remaining)


def duration_ns(intervals: Iterable[TimedInterval]) -> int:
    return sum(interval.end_ns - interval.start_ns for interval in union_intervals(intervals))


def exclusive_engine_intervals(
    step: TimedInterval,
    model_bodies: Iterable[TimedInterval],
    attempt_callbacks: Iterable[TimedInterval],
    store_and_history_bodies: Iterable[TimedInterval],
) -> tuple[TimedInterval, ...]:
    """Return engine-owned wall intervals under the declared public-port boundary.

    Attempt recorder callbacks nested inside a ModelPort body remain engine work,
    except that all actual storage/history method bodies are excluded separately.
    """
    model_engine_intervals = subtract_intervals(model_bodies, attempt_callbacks)
    excluded = union_intervals((*model_engine_intervals, *tuple(store_and_history_bodies)))
    return subtract_intervals((step,), excluded)


def exclusive_engine_duration_ns(
    step: TimedInterval,
    model_bodies: Iterable[TimedInterval],
    attempt_callbacks: Iterable[TimedInterval],
    store_and_history_bodies: Iterable[TimedInterval],
) -> int:
    return duration_ns(
        exclusive_engine_intervals(step, model_bodies, attempt_callbacks, store_and_history_bodies)
    )
