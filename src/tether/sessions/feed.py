"""Per-follower bounded replay workers, independent of the Agent's execution path."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Literal

from tether.types import AttemptIdentity
from tether.types.canonical import canonical
from tether.types.client import (
    Change,
    EntriesAppended,
    Lagged,
    ReadLimit,
    ReplyReset,
    ReplyText,
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
        self.live: dict[tuple[str, str | None], set[LiveBuffer]] = {}
        self.active: dict[tuple[str, str, str], dict[str, AttemptIdentity]] = {}

    async def publish_live(
        self, owner: str, identity: AttemptIdentity, offset: int, text: str
    ) -> None:
        key = (owner, identity.session_id, identity.turn_id)
        self.active.setdefault(key, {})[identity.attempt_id] = identity.model_copy(deep=True)
        event = ReplyText(
            session_id=identity.session_id,
            turn_id=identity.turn_id,
            attempt_id=identity.attempt_id,
            offset=offset,
            text=text,
        )
        for (candidate_owner, selected), buffers in tuple(self.live.items()):
            if candidate_owner != owner or (
                selected is not None and selected != identity.session_id
            ):
                continue
            for buffer in tuple(buffers):
                buffer.put(event)

    def finish_live(
        self,
        owner: str,
        identity: AttemptIdentity,
        reason: Literal["outage", "cancelled", "reconnect"] | None,
    ) -> None:
        key = (owner, identity.session_id, identity.turn_id)
        attempts = self.active.get(key)
        if attempts is not None:
            attempts.pop(identity.attempt_id, None)
            if not attempts:
                self.active.pop(key, None)
        for (candidate_owner, selected), buffers in tuple(self.live.items()):
            if candidate_owner != owner or (
                selected is not None and selected != identity.session_id
            ):
                continue
            for buffer in tuple(buffers):
                buffer.finish(identity, reason)

    def _subscribe(self, owner: str, session_id: str | None, buffer: LiveBuffer) -> None:
        self.live.setdefault((owner, session_id), set()).add(buffer)
        for (candidate_owner, candidate_session, _), attempts in tuple(self.active.items()):
            if candidate_owner != owner or (
                session_id is not None and candidate_session != session_id
            ):
                continue
            for identity in attempts.values():
                buffer.reset(identity, "reconnect")

    def _unsubscribe(self, owner: str, session_id: str | None, buffer: LiveBuffer) -> None:
        key = (owner, session_id)
        buffers = self.live.get(key)
        if buffers is not None:
            buffers.discard(buffer)
            if not buffers:
                self.live.pop(key, None)

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
        live: LiveBuffer,
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
                    for (owner, active_session, _), attempts in tuple(self.active.items()):
                        if owner != actor.id or (
                            session_id is not None and active_session != session_id
                        ):
                            continue
                        for identity in attempts.values():
                            live.reset(identity, "reconnect")
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
        await self.access.authorize(
            actor,
            resource,
            session_id=session_id,
        )
        position = await self.access.watermark(actor) if since is None else since
        queue: asyncio.Queue[PersistedChange | BaseException | Lagged | ResyncRequired] = (
            asyncio.Queue(self.access.limits.follower_queue_size)
        )
        live = LiveBuffer(self.access.limits.follower_queue_size, self.access.limits.max_read_bytes)
        # No await between claiming capacity and registering the producer.
        if len(self.tasks) >= self.access.limits.max_followers:
            raise ReadLimit("follower_limit")
        task = asyncio.create_task(self._produce(actor, session_id, position, queue, live))
        self.tasks.add(task)
        self._subscribe(actor.id, session_id, live)
        try:
            while True:
                for item in await self._next(queue, live, task):
                    if isinstance(item, BaseException):
                        raise item
                    values = variants(item) if isinstance(item, PersistedChange) else (item,)
                    for value in values:
                        self.access.check()
                        await self.access.authorize(
                            actor,
                            resource,
                            session_id=session_id,
                        )
                        if isinstance(item, PersistedChange):
                            await self.access.authorize(
                                actor, item.session_id, session_id=item.session_id
                            )
                        elif isinstance(value, (ReplyText, ReplyReset)):
                            await self.access.authorize(
                                actor, value.session_id, session_id=value.session_id
                            )
                        self.access.size(value)
                        yield value
        finally:
            self._unsubscribe(actor.id, session_id, live)
            live.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.discard(task)

    @staticmethod
    async def _next(
        queue: asyncio.Queue[PersistedChange | BaseException | Lagged | ResyncRequired],
        live: LiveBuffer,
        producer: asyncio.Task[None],
    ) -> tuple[
        PersistedChange | BaseException | Lagged | ResyncRequired | ReplyText | ReplyReset,
        ...,
    ]:
        reader = asyncio.create_task(queue.get())
        live_reader = asyncio.create_task(live.get())
        try:
            done, _ = await asyncio.wait(
                (reader, live_reader, producer), return_when=asyncio.FIRST_COMPLETED
            )
            if reader in done and live_reader in done:
                persisted, provisional = reader.result(), live_reader.result()
                if isinstance(persisted, ResyncRequired):
                    return persisted, provisional
                if isinstance(provisional, (ReplyReset, Lagged)):
                    return provisional, persisted
                return provisional, persisted
            if live_reader in done:
                return (live_reader.result(),)
            if reader in done:
                return (reader.result(),)
            raise RuntimeError("follower_closed")
        finally:
            reader.cancel()
            live_reader.cancel()
            await asyncio.gather(reader, live_reader, return_exceptions=True)


class LiveBuffer:
    """Finite live-only queue; it cannot backpressure stored changes or the Engine."""

    def __init__(self, capacity: int, max_event_bytes: int) -> None:
        self.capacity, self.max_event_bytes = capacity, max_event_bytes
        self.values: deque[ReplyText | ReplyReset | Lagged] = deque()
        self.ready = asyncio.Event()
        self.missed = 0
        self.missed_before = False
        self.blocked: set[str] = set()
        self.closed_attempts: set[str] = set()
        self.closed = False

    def put(self, value: ReplyText) -> None:
        if self.closed or value.attempt_id in self.blocked | self.closed_attempts:
            return
        if len(canonical(value.model_dump(mode="json"))) > self.max_event_bytes:
            self._lose(value.attempt_id)
            return
        if self.missed and len(self.values) < self.capacity:
            self.values.append(Lagged(missed=self.missed))
            self.missed = 0
        if self.values and isinstance(self.values[-1], ReplyText):
            last = self.values[-1]
            if (
                last.session_id == value.session_id
                and last.turn_id == value.turn_id
                and last.attempt_id == value.attempt_id
                and last.offset + len(last.text) == value.offset
            ):
                combined = last.model_copy(update={"text": last.text + value.text})
                if len(canonical(combined.model_dump(mode="json"))) > self.max_event_bytes:
                    self._lose(value.attempt_id)
                    return
                self.values[-1] = combined
                self.ready.set()
                return
        if len(self.values) >= self.capacity:
            self._lose(value.attempt_id)
            return
        self.values.append(value)
        self.ready.set()

    def _lose(self, attempt_id: str) -> None:
        self.blocked.add(attempt_id)
        if len(self.values) < self.capacity:
            self.values.append(Lagged(missed=1))
        else:
            self.missed += 1
        self.ready.set()

    def reset(
        self,
        identity: AttemptIdentity,
        reason: Literal["outage", "cancelled", "reconnect"],
    ) -> None:
        self.blocked.add(identity.attempt_id)
        self.closed_attempts.add(identity.attempt_id)
        self.values = deque(
            item
            for item in self.values
            if not isinstance(item, ReplyText) or item.attempt_id != identity.attempt_id
        )
        event = ReplyReset(
            session_id=identity.session_id,
            turn_id=identity.turn_id,
            attempt_id=identity.attempt_id,
            reason=reason,
        )
        if len(self.values) >= self.capacity:
            dropped = self.values.popleft()
            if isinstance(dropped, ReplyText):
                self.missed += 1
            elif isinstance(dropped, Lagged):
                self.missed += dropped.missed
            else:
                self.missed += 1
            self.missed_before = True
        self.values.append(event)
        self.ready.set()

    def finish(
        self,
        identity: AttemptIdentity,
        reason: Literal["outage", "cancelled", "reconnect"] | None,
    ) -> None:
        self.closed_attempts.add(identity.attempt_id)
        if reason is not None:
            self.reset(identity, reason)
        if self.values:
            self.ready.set()

    async def get(self) -> ReplyText | ReplyReset | Lagged:
        if self.missed_before and self.missed:
            self.missed_before = False
            value = Lagged(missed=self.missed)
            self.missed = 0
            return value
        while not self.values:
            if self.closed:
                raise RuntimeError("follower_closed")
            self.ready.clear()
            await self.ready.wait()
        value = self.values.popleft()
        if self.missed and len(self.values) < self.capacity:
            self.values.append(Lagged(missed=self.missed))
            self.missed = 0
        if self.values:
            self.ready.set()
        return value

    def close(self) -> None:
        self.closed = True
        self.ready.set()
