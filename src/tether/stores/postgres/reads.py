"""Indexed caller-scoped reads; all semantic writers serialize on the owner head."""

from typing import Literal

from psycopg import sql

from tether.stores.indexing import entry_groups
from tether.types.execution import Entry
from tether.types.storage import (
    EntryPage,
    IntegrityError,
    SessionSnapshot,
    SessionView,
    StoreError,
    UnknownSession,
)

from . import row_codec
from .rows import Connection, Rows


class Reads(Rows):
    def limit(self, limit: int) -> None:
        self.root.check_loop()
        if not 0 < limit <= self.root.limits.page_limit:
            raise ValueError("invalid_page")

    async def scope(self, connection: Connection, session_id: str) -> int:
        sequence, _ = await self.head(connection, write=False)
        if sequence == 0:
            raise UnknownSession("unknown_session")
        cursor = await connection.execute(
            sql.SQL("SELECT entry_count FROM {} WHERE owner=%s AND id=%s").format(
                self.table("sessions")
            ),
            (self.owner, session_id.encode()),
        )
        row = await cursor.fetchone()
        if row is None:
            raise UnknownSession("unknown_session")
        if not isinstance(row[0], int) or row[0] < 0:
            raise IntegrityError("projection_integrity")
        return row[0]

    async def get(self, session_id: str) -> SessionView | None:
        async def read(connection: Connection) -> SessionView | None:
            sequence, _ = await self.head(connection, write=False)
            if sequence == 0:
                return None
            try:
                frame = await self.frame(connection, session_id, write=False)
            except UnknownSession:
                return None
            if not 0 < frame.projection.summary.last_seq <= sequence:
                raise IntegrityError("projection_sequence_integrity")
            return await self.view(connection, frame)

        return await self.root.run(self.actor, session_id, read)

    async def snapshot(self, session_id: str) -> SessionSnapshot:
        async def read(connection: Connection) -> SessionSnapshot:
            sequence, _ = await self.head(connection, write=False)
            if sequence == 0:
                raise UnknownSession("unknown_session")
            frame = await self.frame(connection, session_id, write=False)
            if not 0 < frame.projection.summary.last_seq <= sequence:
                raise IntegrityError("projection_sequence_integrity")
            return SessionSnapshot(
                view=await self.view(connection, frame),
                through_index=frame.projection.summary.entry_count - 1,
                seq=sequence,
            )

        return await self.root.run(self.actor, session_id, read)

    async def watermark(self) -> int:
        async def read(connection: Connection) -> int:
            return (await self.head(connection, write=False))[0]

        return await self.root.run(self.actor, None, read)

    async def entry_rows(
        self,
        connection: Connection,
        selected: sql.Composed,
        params: tuple[object, ...],
        *,
        limit: int,
        budget: int,
        reverse: bool = False,
        complete: bool = False,
        byte_reason: str = "entry_read_bytes",
    ) -> tuple[tuple[Entry, ...], int | None]:
        order = sql.SQL("DESC" if reverse else "ASC")
        cursor = await connection.execute(
            sql.SQL(
                "WITH selected AS ({}) SELECT position, CASE WHEN "
                "row_number() OVER (ORDER BY position {})<=%s AND "
                "sum(octet_length(envelope)) OVER (ORDER BY position {})<=%s "
                "THEN envelope END FROM selected ORDER BY position {}"
            ).format(selected, order, order, order),
            (*params, limit, budget),
        )
        rows = await cursor.fetchall()
        if complete and len(rows) > limit:
            raise StoreError("lookup_limit")
        if any(not isinstance(row[1], bytes) for row in rows[:limit]):
            raise StoreError(byte_reason)
        entries = tuple(row_codec.entry(row[1], row[0]) for row in rows[:limit])
        following = rows[-1][0] if len(rows) > limit else None
        if following is not None and not isinstance(following, int):
            raise IntegrityError("entry_integrity")
        return entries, following

    async def entries(
        self,
        session_id: str,
        *,
        start: int,
        limit: int,
        reverse: bool = False,
    ) -> EntryPage:
        self.limit(limit)
        if start < 0:
            raise ValueError("invalid_page")

        async def read(connection: Connection) -> EntryPage:
            count = await self.scope(connection, session_id)
            first = min(start, count - 1) if reverse else start
            values, following = await self.entry_rows(
                connection,
                sql.SQL(
                    "SELECT position,envelope FROM {} WHERE owner=%s AND session=%s "
                    "AND position {} %s ORDER BY position {} LIMIT %s"
                ).format(
                    self.table("entries"),
                    sql.SQL("<=" if reverse else ">="),
                    sql.SQL("DESC" if reverse else "ASC"),
                ),
                (self.owner, session_id.encode(), first, limit + 1),
                limit=limit,
                budget=self.root.limits.max_batch_bytes,
                reverse=reverse,
            )
            expected = range(first, -1, -1) if reverse else range(first, count)
            positions = tuple(expected[: limit + 1])
            actual = tuple(entry.index for entry in values)
            if actual != positions[:limit] or following != (
                positions[limit] if len(positions) > limit else None
            ):
                raise IntegrityError("entry_order_invalid")
            return EntryPage(items=values, next_start=following)

        return await self.root.run(self.actor, session_id, read)

    async def lookup(
        self,
        session_id: str,
        *,
        kind: Literal["key", "invocation", "model_call", "turn", "history_link"],
        value: str,
        limit: int = 1000,
        max_bytes: int | None = None,
    ) -> tuple[Entry, ...]:
        self.limit(limit)
        if kind not in {"key", "invocation", "model_call", "turn", "history_link"}:
            raise ValueError("invalid_lookup_kind")
        maximum = self.root.limits.max_batch_bytes if max_bytes is None else max_bytes
        if maximum <= 0:
            raise ValueError("invalid_read_bytes")
        maximum = min(maximum, self.root.limits.max_batch_bytes)

        async def read(connection: Connection) -> tuple[Entry, ...]:
            count = await self.scope(connection, session_id)
            if kind == "key":
                selected = sql.SQL(
                    "SELECT position,envelope FROM {} WHERE "
                    "owner=%s AND session=%s AND entry_key=%s ORDER BY position LIMIT %s"
                ).format(self.table("entries"))
                params = (self.owner, session_id.encode(), value.encode(), limit + 1)
            else:
                selected = sql.SQL(
                    "SELECT e.position,e.envelope FROM {} g JOIN {} e "
                    "ON e.owner=g.owner AND e.session=g.session AND e.position=g.position "
                    "WHERE g.owner=%s AND g.session=%s AND g.kind=%s AND g.value=%s "
                    "ORDER BY e.position LIMIT %s"
                ).format(self.table("entry_groups"), self.table("entries"))
                params = (self.owner, session_id.encode(), kind, value.encode(), limit + 1)
            entries, _ = await self.entry_rows(
                connection,
                selected,
                params,
                limit=limit,
                budget=maximum,
                complete=True,
                byte_reason="lookup_bytes",
            )
            if any(
                entry.index >= count
                or (
                    entry.key != value
                    if kind == "key"
                    else (kind, value) not in entry_groups(session_id, entry)
                )
                for entry in entries
            ):
                raise IntegrityError("lookup_integrity")
            return entries

        return await self.root.run(self.actor, session_id, read)
