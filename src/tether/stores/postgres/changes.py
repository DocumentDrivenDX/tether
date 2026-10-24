"""Persisted owner-sequence polling; filtering never skips unobserved owner changes."""

import asyncio
from collections.abc import AsyncGenerator, Sequence

from psycopg import sql

from tether.types.canonical import canonical
from tether.types.storage import ChangeGap, IntegrityError, PersistedChange, StoreError

from . import row_codec
from .effects import Effects
from .rows import Connection


def change(row: tuple[object, ...], owner: str) -> PersistedChange:
    if not isinstance(row[1], bytes) or not isinstance(row[2], bytes):
        raise IntegrityError("change_integrity")
    try:
        value = PersistedChange.model_validate_json(row[2])
    except ValueError:
        raise IntegrityError("change_integrity") from None
    if (
        value.seq != row[0]
        or value.session_id.encode() != row[1]
        or canonical(value.model_dump(mode="json")) != row[2]
    ):
        raise IntegrityError("change_integrity")
    for entry in value.entries:
        row_codec.entry(canonical(entry.model_dump(mode="json")), entry.index)
    if value.summary is not None:
        summary = row_codec.summary(canonical(value.summary.model_dump(mode="json")), row[1], owner)
        indexes = tuple(entry.index for entry in value.entries)
        expected = tuple(range(summary.entry_count - len(value.entries), summary.entry_count))
        if (
            summary.last_seq != value.seq
            or summary.entry_count < len(value.entries)
            or indexes != expected
        ):
            raise IntegrityError("change_integrity")
    elif value.kind != "removed":
        raise IntegrityError("change_integrity")
    return value


def prefix(
    rows: Sequence[tuple[object, ...]],
    position: int,
    owner: str,
) -> tuple[tuple[PersistedChange, ...], int]:
    values: list[PersistedChange] = []
    through = position
    for row in rows:
        if not isinstance(row[1], bool):
            raise IntegrityError("change_integrity")
        if row[1]:
            if row[2] is None or row[3] is None:
                if not values:
                    raise StoreError("change_read_bytes")
                break
            values.append(change((row[0], row[2], row[3]), owner))
        through = row[0]
    if not isinstance(through, int):
        raise IntegrityError("change_integrity")
    return tuple(values), through


class Changes(Effects):
    async def poll(
        self, position: int, *, session_id: str | None = None
    ) -> tuple[tuple[PersistedChange, ...], int]:
        async def read(connection: Connection) -> tuple[tuple[PersistedChange, ...], int]:
            head, floor = await self.head(connection, write=False)
            if position < floor:
                raise ChangeGap("too_old")
            if position > head:
                raise IntegrityError("owner_head_integrity")
            maximum = self.root.limits.max_batch_bytes
            picked = sql.SQL("true") if session_id is None else sql.SQL("session=%s")
            selection = () if session_id is None else (session_id.encode(),)
            cursor = await connection.execute(
                sql.SQL(
                    "WITH selected AS (SELECT seq,session,envelope,{} AS picked FROM {} "
                    "WHERE owner=%s AND seq>%s AND seq<=%s ORDER BY seq LIMIT %s), "
                    "bounded AS (SELECT *,sum(CASE WHEN picked THEN octet_length(session)+"
                    "octet_length(envelope) ELSE 0 END) OVER (ORDER BY seq) AS size FROM selected) "
                    "SELECT seq,picked,CASE WHEN picked AND size<=%s THEN session END, "
                    "CASE WHEN picked AND size<=%s THEN envelope END FROM bounded ORDER BY seq"
                ).format(picked, self.table("changes")),
                (
                    *selection,
                    self.owner,
                    position,
                    head,
                    self.root.limits.page_limit,
                    maximum,
                    maximum,
                ),
            )
            rows = await cursor.fetchall()
            if tuple(row[0] for row in rows) != tuple(
                range(position + 1, position + len(rows) + 1)
            ):
                raise IntegrityError("change_sequence_integrity")
            if len(rows) < self.root.limits.page_limit and position + len(rows) < head:
                raise IntegrityError("change_sequence_integrity")
            return prefix(rows, position, self.actor.id)

        return await self.root.run(self.actor, session_id, read)

    async def changes(
        self,
        *,
        session_id: str | None = None,
        since: int | None = None,
    ) -> AsyncGenerator[PersistedChange, None]:
        async def initialize(connection: Connection) -> int:
            head, _ = await self.head(connection, write=False)
            if session_id is not None:
                await self.scope(connection, session_id)
            if since is not None and (since < 0 or since > head):
                raise ValueError("invalid_sequence")
            return head if since is None else since

        position = await self.root.run(self.actor, session_id, initialize)
        while True:
            values, position = await self.poll(position, session_id=session_id)
            for value in values:
                if session_id is None or value.session_id == session_id:
                    yield value
            if not values:
                await asyncio.sleep(self.root.limits.change_poll_seconds)
