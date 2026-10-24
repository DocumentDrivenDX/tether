"""Known lease acquisition and retained metadata cleanup share one exception scope."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from .capture import Capture
from .leases import Token


async def finish(store: Capture, token: Token) -> None:
    task = asyncio.create_task(store.release(token))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    try:
        task.result()
    finally:
        if cancelled:
            raise asyncio.CancelledError


@asynccontextmanager
async def lease(store: Capture, session_id: str, turn_id: str) -> AsyncGenerator[Token, None]:
    token, _ = await store.acquire(session_id, turn_id=turn_id)
    try:
        yield token
    except BaseException:
        # Cleanup is observed; refusal leaves expiry recovery and the original failure.
        with suppress(BaseException):
            await finish(store, token)
        raise
    else:
        await finish(store, token)
