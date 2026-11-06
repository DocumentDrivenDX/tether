"""Presentation recovery for an injected, borrowed client feed."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from tether.client import (
    Change,
    ClientProtocol,
    Lagged,
    ReplyReset,
    ReplyText,
    ResyncRequired,
    SessionUpdated,
)


@dataclass(frozen=True)
class FeedPresentation:
    selected: Callable[[], str | None]
    refresh: Callable[[], Awaitable[None]]
    show: Callable[[], Awaitable[None]]
    reset: Callable[[], None]
    reply: Callable[[ReplyText], None]
    reset_attempt: Callable[[ReplyReset], None]
    failed: Callable[[], None]
    busy: Callable[[], bool] = lambda: False
    stopping: Callable[[], bool] = lambda: False


async def _resnapshot(client: ClientProtocol, view: FeedPresentation) -> int | None:
    selected = view.selected()
    snapshot = await client.snapshot(selected) if selected is not None else None
    if view.stopping():
        return snapshot.seq if snapshot is not None else None
    await view.refresh()
    if view.stopping():
        return snapshot.seq if snapshot is not None else None
    await view.show()
    return snapshot.seq if snapshot is not None else None


async def _close_stream(stream: AsyncIterator[Change]) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


async def _deliver(change: Change, view: FeedPresentation) -> None:
    if isinstance(change, ReplyText):
        view.reply(change)
    elif isinstance(change, ReplyReset):
        view.reset_attempt(change)
    elif isinstance(change, SessionUpdated):
        await view.refresh()
        if view.stopping():
            return
        if (
            change.summary.id == view.selected()
            and change.summary.status != "running"
            and not view.busy()
        ):
            await view.show()


async def _follow_once(
    client: ClientProtocol, view: FeedPresentation, cursor: int | None
) -> tuple[bool, bool]:
    """Return (needs_resnapshot, observed_change)."""
    if view.stopping():
        return False, False
    stream = client.follow(since=cursor)
    observed_change = False
    try:
        async for change in stream:
            if view.stopping():
                return False, observed_change
            if isinstance(change, (Lagged, ResyncRequired)):
                view.reset()
                return True, observed_change
            observed_change = True
            await _deliver(change, view)
            # Presentation callbacks may consume cancellation while a borrowed
            # read completes. Do not advance the iterator after shutdown has
            # claimed ownership of this follower.
            if view.stopping():
                return False, observed_change
        if view.stopping():
            return False, observed_change
        view.reset()
        view.failed()
        return False, observed_change
    finally:
        await _close_stream(stream)


async def follow_client(
    client: ClientProtocol,
    view: FeedPresentation,
    *,
    initial_cursor: int | None = None,
) -> None:
    """Reload after explicit gaps; never retry a command or close the borrower."""
    cursor = initial_cursor
    restarts = 0
    try:
        while not view.stopping():
            resync, observed_change = await _follow_once(client, view, cursor)
            if observed_change:
                restarts = 0
            if not resync:
                return
            if view.stopping():
                return
            restarts += 1
            if restarts > 5:
                view.failed()
                return
            cursor = await _resnapshot(client, view)
            if view.stopping():
                return
            await asyncio.sleep(min(0.1 * 2**restarts, 1))
    except asyncio.CancelledError:
        raise
    except Exception:
        if view.stopping():
            return
        view.reset()
        view.failed()
