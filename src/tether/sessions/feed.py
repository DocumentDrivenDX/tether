"""Per-follower bounded replay workers, independent of the Agent's execution path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing

from tether.types.client import (
    Change,
    EntriesAppended,
    Lagged,
    ReadLimit,
    ResyncRequired,
    SessionRemoved,
    SessionUpdated,
)
from tether.types.execution import Actor, copy_actor
from tether.types.storage import ChangeGap, PersistedChange

from .access import SessionAccess


def variants(change: PersistedChange) -> tuple[Change, ...]:
    if change.kind == "removed":
        return (SessionRemoved(seq=change.seq, session_id=change.session_id, last_in_commit=True),)
    values: list[Change] = []
    if change.summary is not None:
        values.append(
            SessionUpdated(
                seq=change.seq, summary=change.summary, last_in_commit=not change.entries
            )
        )
    if change.entries:
        values.append(
            EntriesAppended(
                seq=change.seq,
                session_id=change.session_id,
                entries=change.entries,
                last_in_commit=True,
            )
        )
    return tuple(values)


class Followers:
    def __init__(self, access: SessionAccess) -> None:
        self.access = access
        self.tasks: set[asyncio.Task[None]] = set()

    async def close(self) -> None:
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _produce(
        self,
        actor: Actor,
        session_id: str | None,
        since: int,
        queue: asyncio.Queue[PersistedChange | BaseException | Lagged | ResyncRequired],
    ) -> None:
        position = since
        try:
            while True:
                try:
                    async with aclosing(
                        self.access.store_for(actor).changes(
                            actor.id, session_id=session_id, since=position
                        )
                    ) as changes:
                        async for change in changes:
                            self.access.size(change)
                            if queue.full():
                                await queue.put(Lagged(missed=0))
                            await queue.put(change)
                            position = change.seq
                except ChangeGap:
                    position = await self.access.watermark(actor)
                    await queue.put(ResyncRequired(reason="too_old"))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await queue.put(error)

    async def follow(
        self, actor: Actor, *, session_id: str | None, since: int | None
    ) -> AsyncIterator[Change]:
        self.access.check()
        if since is not None:
            self.access.position(since)
        if len(self.tasks) >= self.access.limits.max_followers:
            raise ReadLimit("follower_limit")
        actor = copy_actor(actor)
        resource = session_id if session_id is not None else "owner:" + actor.id
        await self.access.authorize(actor, resource)
        position = await self.access.watermark(actor) if since is None else since
        queue: asyncio.Queue[PersistedChange | BaseException | Lagged | ResyncRequired] = (
            asyncio.Queue(self.access.limits.follower_queue_size)
        )
        # No await between claiming capacity and registering the producer.
        if len(self.tasks) >= self.access.limits.max_followers:
            raise ReadLimit("follower_limit")
        task = asyncio.create_task(self._produce(actor, session_id, position, queue))
        self.tasks.add(task)
        try:
            while True:
                item = await self._next(queue, task)
                if isinstance(item, BaseException):
                    raise item
                values = variants(item) if isinstance(item, PersistedChange) else (item,)
                for value in values:
                    self.access.check()
                    await self.access.authorize(actor, resource)
                    if isinstance(item, PersistedChange):
                        await self.access.authorize(actor, item.session_id)
                    self.access.size(value)
                    yield value
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.discard(task)

    @staticmethod
    async def _next(
        queue: asyncio.Queue[PersistedChange | BaseException | Lagged | ResyncRequired],
        producer: asyncio.Task[None],
    ) -> PersistedChange | BaseException | Lagged | ResyncRequired:
        reader = asyncio.create_task(queue.get())
        try:
            done, _ = await asyncio.wait((reader, producer), return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                return reader.result()
            raise RuntimeError("follower_closed")
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
