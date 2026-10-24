"""Bounded canonical journal capture; reconstruction runs after database resources exit."""

from dataclasses import dataclass

from psycopg import sql

from tether.stores.projections import SessionIndex, create_index
from tether.types.storage import IntegrityError, SessionView, StoreError

from . import row_codec
from .leases import Leases, Token, fence
from .rows import Connection, Frame


@dataclass(frozen=True)
class Captured:
    frame: Frame
    view: SessionView
    rows: tuple[tuple[int, bytes], ...]


def rebuild(captured: Captured) -> SessionIndex:
    projection = captured.frame.projection
    summary = projection.summary
    index = create_index(summary.id, summary.owner, projection.init, summary.created_at)
    for position, data in captured.rows:
        index.apply(row_codec.entry(data, position))
    index.summary = index.summary.model_copy(update={"last_seq": summary.last_seq})
    if (
        index.view() != captured.view
        or index.state != projection.state
        or index.missing_usage != captured.frame.missing_usage
    ):
        raise IntegrityError("projection_integrity")
    return index


class Capture(Leases):
    async def capture(self, token: Token) -> Captured:
        async def read(connection: Connection) -> Captured:
            head, _ = await self.head(connection, write=False)
            frame = await self.frame(connection, token.session_id, write=False)
            if frame.projection.summary.last_seq > head:
                raise IntegrityError("projection_sequence_integrity")
            fence(frame, token, await self.clock(connection))
            count = frame.projection.summary.entry_count
            cursor = await connection.execute(
                sql.SQL(
                    "SELECT EXISTS(SELECT 1 FROM {} WHERE owner=%s AND session=%s "
                    "AND (position<0 OR position>=%s))"
                ).format(self.table("entries")),
                (self.owner, token.session_id.encode(), count),
            )
            if await cursor.fetchone() != (False,):
                raise IntegrityError("entry_order_invalid")
            rows = await self.capture_rows(connection, token.session_id, count, frame.bytes_used)
            view = await self.view(connection, frame)
            fence(frame, token, await self.clock(connection))
            return Captured(frame, view, rows)

        return await self.root.run(self.actor, token.session_id, read)

    async def capture_rows(
        self,
        connection: Connection,
        session_id: str,
        count: int,
        used: int,
    ) -> tuple[tuple[int, bytes], ...]:
        values: list[tuple[int, bytes]] = []
        if used > self.root.limits.max_index_bytes:
            raise StoreError("turn_snapshot_bytes")
        for _ in range(self.root.limits.max_startup_batches):
            if len(values) == count:
                return tuple(values)
            budget = min(self.root.limits.max_batch_bytes, self.root.limits.max_index_bytes - used)
            if budget <= 0:
                raise StoreError("turn_snapshot_bytes")
            cursor = await connection.execute(
                sql.SQL(
                    "WITH selected AS (SELECT position,envelope FROM {} WHERE "
                    "owner=%s AND session=%s AND position>=%s ORDER BY position LIMIT %s) "
                    "SELECT position,CASE WHEN sum(octet_length(envelope)) OVER "
                    "(ORDER BY position)<=%s THEN envelope END FROM selected ORDER BY position"
                ).format(self.table("entries")),
                (self.owner, session_id.encode(), len(values), self.root.limits.page_limit, budget),
            )
            rows = await cursor.fetchall()
            accepted = prefix(rows, len(values))
            if not accepted:
                raise IntegrityError("entry_order_invalid")
            used += sum(len(data) for _, data in accepted)
            values.extend(accepted)
            if len(values) > count:
                raise IntegrityError("entry_order_invalid")
            if len(values) == count:
                return tuple(values)
        raise StoreError("turn_snapshot_batches")

    async def reconstruct(self, token: Token) -> SessionIndex:
        captured = await self.capture(token)
        index = await self.root.prepare(token.session_id, lambda: rebuild(captured))
        await self.heartbeat(token)
        return index


def prefix(rows: list[tuple[object, ...]], start: int) -> tuple[tuple[int, bytes], ...]:
    values: list[tuple[int, bytes]] = []
    for position, data in rows:
        if position != start + len(values) or not isinstance(position, int):
            raise IntegrityError("entry_order_invalid")
        if data is None:
            if not values:
                raise StoreError("turn_snapshot_bytes")
            break
        if not isinstance(data, bytes):
            raise IntegrityError("entry_integrity")
        values.append((position, data))
    return tuple(values)
