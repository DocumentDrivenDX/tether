"""Test-local clocks, timeout captures and owned-task cleanup for context tests."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


class ContextClock:
    """Replace only ContextStep's loop-time view; keep the real loop untouched."""

    def __init__(self) -> None:
        self.now = 0.0
        self.deadlines: list[float] = []
        self.timers: list[asyncio.Timeout] = []

    def time(self) -> float:
        return self.now

    def timeout_at(self, deadline: float) -> asyncio.Timeout:
        self.deadlines.append(deadline)
        timer = asyncio.Timeout(None)
        self.timers.append(timer)
        return timer

    def install(self, module: Any, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            module,
            "asyncio",
            SimpleNamespace(get_running_loop=lambda: self, timeout_at=self.timeout_at),
        )

    def advance_past(self, deadline: float) -> None:
        self.now = deadline + 0.001


class TimeoutCapture:
    """Capture configured timeout objects and arm them at a held-phase barrier."""

    def __init__(self) -> None:
        self.captured: list[tuple[float, asyncio.Timeout]] = []

    def timeout(self, seconds: float) -> asyncio.Timeout:
        timer = asyncio.Timeout(None)
        self.captured.append((seconds, timer))
        return timer

    def install(self, module: Any, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            module,
            "asyncio",
            SimpleNamespace(timeout=self.timeout, get_running_loop=asyncio.get_running_loop),
        )

    def arm_last(self) -> asyncio.Timeout:
        seconds, timer = self.captured[-1]
        timer.reschedule(asyncio.get_running_loop().time() + seconds)
        return timer


class CancellationObserver:
    """Observe cancellations delivered to one module's shielded operation."""

    def __init__(self, acknowledgements: int = 2) -> None:
        self.entered = asyncio.Event()
        self.acknowledged = tuple(asyncio.Event() for _ in range(acknowledgements))
        self.seen = 0

    def install(self, module: Any, monkeypatch: Any) -> None:
        real_asyncio = asyncio
        owner = self

        async def shield(future: asyncio.Future[Any]) -> Any:
            owner.entered.set()
            try:
                return await real_asyncio.shield(future)
            except asyncio.CancelledError:
                owner.seen += 1
                if owner.seen <= len(owner.acknowledged):
                    owner.acknowledged[owner.seen - 1].set()
                raise

        monkeypatch.setattr(
            module,
            "asyncio",
            SimpleNamespace(
                get_running_loop=real_asyncio.get_running_loop,
                Semaphore=real_asyncio.Semaphore,
                CancelledError=real_asyncio.CancelledError,
                shield=shield,
            ),
        )


async def wait_event_or_task(
    event: asyncio.Event,
    task: asyncio.Future[Any],
    *,
    waiter_started: asyncio.Event | None = None,
    waiter_finished: asyncio.Event | None = None,
) -> None:
    """Wait for entry or surface the owned operation's earlier terminal result."""
    waiter = asyncio.create_task(event.wait())
    if waiter_started is not None:
        waiter_started.set()
    try:
        done, _ = await asyncio.wait((waiter, task), return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            task.result()
            raise AssertionError("operation_ended_before_phase_entry")
    finally:
        if not waiter.done():
            waiter.cancel()
        await settle_tasks((waiter,))
        if waiter_finished is not None:
            waiter_finished.set()


async def settle_tasks(
    tasks: tuple[asyncio.Future[Any], ...],
    *,
    release: Callable[[], None] | None = None,
    close: asyncio.Event | None = None,
    entered: asyncio.Event | None = None,
    started: asyncio.Event | None = None,
    cancellation_acks: tuple[asyncio.Event, ...] = (),
) -> None:
    """Retain every owned task through repeated cancellation, consuming its result."""
    if release is not None:
        release()
    if close is not None and (entered is None or entered.is_set()):
        await close.wait()
    if not tasks:
        return

    async def gather_owned() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    owner = asyncio.create_task(gather_owned())
    if started is not None:
        started.set()
    cancellation = await _settle_owner(owner, cancellation_acks)
    with contextlib.suppress(BaseException):
        owner.result()
    if cancellation is not None:
        raise cancellation


async def _settle_owner(
    owner: asyncio.Task[None], cancellation_acks: tuple[asyncio.Event, ...]
) -> asyncio.CancelledError | None:
    cancellation: asyncio.CancelledError | None = None
    current = asyncio.current_task()
    seen = 0
    while not owner.done():
        try:
            await asyncio.shield(owner)
        except asyncio.CancelledError as error:
            if current is None or current.cancelling() == 0:
                break
            seen += 1
            if seen <= len(cancellation_acks):
                cancellation_acks[seen - 1].set()
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    return cancellation
