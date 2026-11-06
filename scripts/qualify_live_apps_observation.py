"""One explicit caller-controlled watchdog for live observation waits."""

from __future__ import annotations

import asyncio
import contextvars
import math
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

from qualify_live_apps_incomplete import LiveQualificationIncomplete


@dataclass(frozen=True)
class LiveObservationPolicy:
    watchdog_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.watchdog_seconds is not None and (
            not math.isfinite(self.watchdog_seconds) or self.watchdog_seconds < 0
        ):
            raise ValueError("live_observation_watchdog_invalid")


_POLICY: contextvars.ContextVar[LiveObservationPolicy | None] = contextvars.ContextVar(
    "live_observation_policy", default=None
)


@contextmanager
def use_live_observation_policy(policy: LiveObservationPolicy) -> Iterator[None]:
    token = _POLICY.set(policy)
    try:
        yield
    finally:
        _POLICY.reset(token)


@asynccontextmanager
async def observe_until(
    reason: str,
) -> AsyncIterator[None]:
    policy = _POLICY.get()
    watchdog = policy.watchdog_seconds if policy is not None else None
    if watchdog is None:
        yield
        return

    timeout = asyncio.timeout(watchdog)
    try:
        async with timeout:
            yield
    except TimeoutError:
        if timeout.expired():
            raise LiveQualificationIncomplete(reason) from None
        raise


def observation_watchdog_seconds() -> float | None:
    policy = _POLICY.get()
    return policy.watchdog_seconds if policy is not None else None


__all__ = [
    "LiveObservationPolicy",
    "observation_watchdog_seconds",
    "observe_until",
    "use_live_observation_policy",
]
