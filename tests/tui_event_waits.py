"""State barriers for UI tests that must observe operation failure promptly."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast


def let_pilot_screen_wait_use_outer_owner(monkeypatch: Any) -> None:
    """Disable only Textual's omitted screen deadline; retain explicit caller values."""
    from textual.pilot import Pilot

    original = cast(Any, Pilot._wait_for_screen)

    async def wait_for_screen(self: Pilot, **options: float | None) -> bool:
        if "timeout" not in options:
            options["timeout"] = None
        return await original(self, **options)

    monkeypatch.setattr(Pilot, "_wait_for_screen", wait_for_screen)


async def _settle_observer(waiter: asyncio.Task[Any]) -> bool:
    if not waiter.done():
        waiter.cancel()
    drain = asyncio.ensure_future(asyncio.gather(waiter, return_exceptions=True))
    cancelled = False
    while not drain.done():
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError:
            cancelled = True
    drain.result()
    return cancelled


async def wait_for_event_or_operations(
    event: asyncio.Event, *operations: asyncio.Future[Any]
) -> tuple[bool, tuple[asyncio.Future[Any], ...]]:
    """Wait for a state event or return whichever owned operations ended first."""
    event_wait = asyncio.create_task(event.wait())
    cancelled = False
    try:
        done, _ = await asyncio.wait({event_wait, *operations}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if await _settle_observer(event_wait):
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return event_wait in done, tuple(operation for operation in operations if operation in done)


async def wait_for_event_or_operation(event: asyncio.Event, operation: asyncio.Future[Any]) -> None:
    """Wait for state or surface the operation's terminal result without a time limit."""
    state_seen, completed = await wait_for_event_or_operations(event, operation)
    if operation in completed:
        await operation
        if not state_seen:
            raise AssertionError("operation_finished_before_state")


def observe_session_shows(app: Any) -> asyncio.Event:
    """Expose completion of the existing session renderer to a functional test."""
    updated = asyncio.Event()
    original = app._show_session

    async def observed() -> None:
        await original()
        updated.set()

    app._show_session = observed
    return updated


async def wait_for_state_or_operation(
    event: asyncio.Event,
    state: Callable[[], Awaitable[bool]],
    operation: asyncio.Future[Any],
) -> None:
    """Recheck observable state around its event and the consumer's terminal."""
    while True:
        event.clear()
        if await state():
            return
        await wait_for_event_or_operation(event, operation)
