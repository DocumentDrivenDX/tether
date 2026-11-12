"""Deterministic close-entry and retained cleanup controls for resource tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import pytest
from context_lifetime_controls import settle_tasks
from tui_event_waits import wait_for_event_or_operation


class CloseOwner(Protocol):
    async def aclose(self) -> bool: ...

    async def wait_closed(self) -> bool: ...


async def cancel_and_expect_cancellation(task: asyncio.Future[Any]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def close_after_release(
    owner: CloseOwner,
    release: Callable[[], None],
    *tasks: asyncio.Future[Any] | None,
    primary: BaseException | None = None,
) -> None:
    async def teardown() -> bool:
        release()
        owned = tuple(task for task in tasks if task is not None)
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        errors: list[BaseException] = []
        drained = False
        try:
            await owner.aclose()
        except BaseException as error:
            errors.append(error)
        try:
            drained = await owner.wait_closed()
        except BaseException as error:
            errors.append(error)
        if errors:
            raise BaseExceptionGroup("resource_close_and_drain_failed", errors)
        return drained

    cleanup = asyncio.create_task(teardown())
    try:
        await settle_tasks((cleanup,))
        assert cleanup.result()
    except BaseException as error:
        if primary is None:
            raise
        primary.add_note(f"cleanup also failed: {error!r}")


async def wait_closed_entry(
    owner: CloseOwner,
    *,
    cleanup_started: asyncio.Event | None = None,
    cancellation_acks: tuple[asyncio.Event, ...] = (),
) -> asyncio.Task[bool]:
    entered = asyncio.Event()

    async def observe() -> bool:
        entered.set()
        return await owner.wait_closed()

    waiter = asyncio.create_task(observe())
    try:
        await wait_for_event_or_operation(entered, waiter)
    except BaseException as primary:
        waiter.cancel()
        try:
            await settle_tasks(
                (waiter,), started=cleanup_started, cancellation_acks=cancellation_acks
            )
        except BaseException as cleanup_error:
            primary.add_note(f"wait_closed observer cleanup also failed: {cleanup_error!r}")
        raise
    assert not waiter.done()
    return waiter
