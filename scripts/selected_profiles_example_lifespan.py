"""Event and task-terminal coordination for the example ASGI lifespan control."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

from qualify_live_apps_incomplete import LiveQualificationIncomplete
from selected_profiles_process import await_retained

T = TypeVar("T")


async def cancel_and_join(task: asyncio.Future[None]) -> None:
    if not task.done():
        task.cancel()

    async def settle() -> None:
        await asyncio.gather(task, return_exceptions=True)

    await await_retained(asyncio.create_task(settle()))


async def run_thread(operation: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    return await await_retained(
        asyncio.create_task(asyncio.to_thread(partial(operation, *args, **kwargs)))
    )


async def next_message(
    outgoing: asyncio.Queue[dict[str, Any]],
    task: asyncio.Future[None],
    reason: str,
) -> dict[str, Any]:
    message = asyncio.create_task(outgoing.get())
    try:
        done, _ = await asyncio.wait({message, task}, return_when=asyncio.FIRST_COMPLETED)
        if message in done:
            return message.result()
        if task.cancelled():
            raise LiveQualificationIncomplete(reason)
        error = task.exception()
        if error is not None:
            raise error
        raise LiveQualificationIncomplete(reason)
    finally:
        if not message.done():
            message.cancel()

        async def settle_message() -> None:
            await asyncio.gather(message, return_exceptions=True)

        await await_retained(asyncio.create_task(settle_message()))


__all__ = ["cancel_and_join", "next_message", "run_thread"]
