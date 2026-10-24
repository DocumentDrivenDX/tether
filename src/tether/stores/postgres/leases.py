"""Server-time row-generation fences under owner-head then session locks."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg import sql

from tether.types.canonical import canonical, digest
from tether.types.execution import Entry, NewEntry, WorkerOutstanding
from tether.types.storage import (
    IntegrityError,
    LeaseLost,
    RecoveredTurn,
    SessionClosed,
    SessionSummary,
    StoreError,
    TurnInProgress,
)

from .journal import Journal
from .rows import Connection, Frame


@dataclass(frozen=True)
class Token:
    session_id: str
    turn_id: str
    generation: int


@dataclass(frozen=True)
class Prefix:
    count: int
    row_bytes: int


@dataclass(frozen=True)
class Appended:
    entries: tuple[Entry, ...]
    summary: SessionSummary
    prefix: Prefix | None


def matches(frame: Frame, token: Token) -> bool:
    return (
        frame.projection.state.id == token.session_id
        and frame.generation == token.generation
        and frame.lease_turn == token.turn_id
        and frame.lease_until is not None
    )


def fence(frame: Frame, token: Token, now: datetime) -> None:
    if not matches(frame, token) or frame.lease_until is None or frame.lease_until <= now:
        raise LeaseLost("lease_lost")


class Leases(Journal):
    async def write_lease(
        self,
        connection: Connection,
        frame: Frame,
        turn: str | None,
        until: datetime | None,
        generation: int,
    ) -> None:
        size = (
            len(canonical(frame.projection.summary.model_dump(mode="json")))
            + len(frame.content())
            + (len(turn.encode()) if turn is not None else 0)
        )
        if size > self.root.limits.max_batch_bytes:
            raise StoreError("lease_write_bytes")
        cursor = await connection.execute(
            sql.SQL(
                "UPDATE {} SET generation=%s,lease_turn=%s,lease_until=%s "
                "WHERE owner=%s AND id=%s AND generation=%s"
            ).format(self.table("sessions")),
            (
                generation,
                turn.encode() if turn is not None else None,
                until,
                self.owner,
                frame.projection.state.id.encode(),
                frame.generation,
            ),
        )
        if cursor.rowcount != 1:
            raise LeaseLost("lease_lost")
        frame.generation, frame.lease_turn, frame.lease_until = generation, turn, until
        frame.bytes_used = size

    async def end_phase(
        self,
        connection: Connection,
        frame: Frame,
        now: datetime,
    ) -> RecoveredTurn | None:
        active = frame.projection.summary.active_turn_id
        if active is None:
            return None
        sid = frame.projection.state.id
        key = "abandoned:" + digest([sid, frame.generation, active])
        _, inserted = await self.append(
            connection,
            frame,
            [
                NewEntry(
                    kind="turn_ended",
                    key=key,
                    turn_id=active,
                    facts={"stop_reason": "interrupted"},
                )
            ],
            now,
        )
        if not inserted:
            raise IntegrityError("projection_integrity")
        await self.publish(connection, frame, inserted)
        return RecoveredTurn(session_id=sid, turn_id=active, resumable=True)

    async def acquire(self, session_id: str, *, turn_id: str) -> tuple[Token, Frame]:
        async def acquire(connection: Connection) -> tuple[Token, Frame]:
            await self.head(connection, write=True)
            frame = await self.frame(connection, session_id, write=True)
            now = await self.clock(connection)
            if frame.projection.summary.status == "closed":
                raise SessionClosed("session_closed")
            if self.root.workers.busy(session_id):
                raise WorkerOutstanding("worker_outstanding")
            if frame.lease_until is not None and frame.lease_until > now:
                raise TurnInProgress("turn_in_progress")
            if frame.generation == 2**63 - 1:
                raise StoreError("lease_generation_exhausted")
            await self.end_phase(connection, frame, now)
            now = await self.clock(connection)
            generation = frame.generation + 1
            await self.write_lease(
                connection,
                frame,
                turn_id,
                now + timedelta(seconds=self.root.limits.lease_seconds),
                generation,
            )
            token = Token(session_id, turn_id, generation)
            fence(frame, token, await self.clock(connection))
            return token, frame

        return await self.root.run(self.actor, session_id, acquire, write=True)

    async def append_fenced(self, token: Token, values: Sequence[NewEntry]) -> tuple[Entry, ...]:
        return (await self.commit(token, values)).entries

    async def commit(
        self, token: Token, values: Sequence[NewEntry], *, prefix: Prefix | None = None
    ) -> Appended:
        values = tuple(value.model_copy(deep=True) for value in values)

        async def append(connection: Connection) -> Appended:
            await self.head(connection, write=True)
            frame = await self.frame(connection, token.session_id, write=True)
            now = await self.clock(connection)
            fence(frame, token, now)
            if prefix is not None and prefix.count != frame.projection.summary.entry_count:
                raise IntegrityError("turn_prefix_integrity")
            accepted, inserted = await self.append(connection, frame, values, now)
            fence(frame, token, await self.clock(connection))
            if inserted:
                await self.publish(connection, frame, inserted)
            budget = self.growth(frame, inserted, prefix)
            fence(frame, token, await self.clock(connection))
            return Appended(accepted, frame.projection.summary, budget)

        return await self.root.run(self.actor, token.session_id, append, write=True)

    def growth(
        self, frame: Frame, inserted: tuple[Entry, ...], prefix: Prefix | None
    ) -> Prefix | None:
        if prefix is None:
            return None
        if prefix.row_bytes < 0 or prefix.count < 0:
            raise IntegrityError("turn_prefix_integrity")
        rows = prefix.row_bytes + sum(
            len(canonical(entry.model_dump(mode="json"))) for entry in inserted
        )
        count = frame.projection.summary.entry_count
        limits = self.root.limits
        if rows + frame.bytes_used > limits.max_index_bytes:
            raise StoreError("turn_snapshot_bytes")
        if count > limits.page_limit * limits.max_startup_batches:
            raise StoreError("turn_snapshot_count")
        return Prefix(count, rows)

    async def heartbeat(self, token: Token) -> None:
        async def heartbeat(connection: Connection) -> None:
            frame = await self.frame(connection, token.session_id, write=True)
            now = await self.clock(connection)
            fence(frame, token, now)
            await self.renew(connection, frame, token)
            fence(frame, token, await self.clock(connection))

        await self.root.run(self.actor, token.session_id, heartbeat, write=True)

    async def renew(self, connection: Connection, frame: Frame, token: Token) -> None:
        cursor = await connection.execute(
            sql.SQL(
                "UPDATE {} SET lease_until=clock_timestamp()+%s WHERE owner=%s AND id=%s "
                "AND generation=%s AND lease_turn=%s AND lease_until>clock_timestamp() "
                "RETURNING lease_until"
            ).format(self.table("sessions")),
            (
                timedelta(seconds=self.root.limits.lease_seconds),
                self.owner,
                token.session_id.encode(),
                token.generation,
                token.turn_id.encode(),
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise LeaseLost("lease_lost")
        if not isinstance(row[0], datetime):
            raise IntegrityError("lease_integrity")
        frame.lease_until = row[0]

    async def release(self, token: Token) -> None:
        async def release(connection: Connection) -> None:
            await self.head(connection, write=True)
            frame = await self.frame(connection, token.session_id, write=True)
            if not matches(frame, token):
                return
            await self.end_phase(connection, frame, await self.clock(connection))
            await self.write_lease(connection, frame, None, None, frame.generation)

        await self.root.run(self.actor, token.session_id, release, write=True)

    async def recover_one(self, session_id: str, *, now: datetime) -> RecoveredTurn | None:
        if now.tzinfo is None:
            raise ValueError("invalid_recovery_datetime")

        async def recover(connection: Connection) -> RecoveredTurn | None:
            return await self.recover_locked(connection, session_id, now)

        return await self.root.run(self.actor, session_id, recover, write=True)

    async def recover_locked(
        self, connection: Connection, session_id: str, now: datetime
    ) -> RecoveredTurn | None:
        await self.head(connection, write=True)
        frame = await self.frame(connection, session_id, write=True)
        at = min(now, await self.clock(connection))
        if frame.lease_until is not None and frame.lease_until > at:
            return None
        if self.root.workers.busy(session_id):
            raise WorkerOutstanding("worker_outstanding")
        recovered = await self.end_phase(connection, frame, at)
        if frame.lease_turn is not None:
            await self.write_lease(connection, frame, None, None, frame.generation)
        return recovered
