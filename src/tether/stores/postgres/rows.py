"""Internal locked SQL primitives; not a qualified public SessionStore yet."""

from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import AsyncConnection, sql
from psycopg.errors import UniqueViolation

from tether.stores.indexing import pending_confirmation
from tether.stores.projection import Projection, new_projection
from tether.types.canonical import canonical
from tether.types.execution import Actor, Entry, EntryConflict, copy_actor
from tether.types.storage import (
    IntegrityError,
    PersistedChange,
    SessionInit,
    SessionSummary,
    SessionView,
    StoredProjection,
    StoreError,
    UnknownSession,
)

from .root import DatabaseRoot

Connection = AsyncConnection[tuple[object, ...]]


@dataclass
class Frame:
    projection: Projection
    missing_usage: int = 0
    generation: int = 0
    lease_turn: str | None = None
    lease_until: datetime | None = None
    bytes_used: int = 0

    def content(self) -> bytes:
        value = StoredProjection(
            state=self.projection.state,
            init=self.projection.init,
            specs=self.projection.specs,
            missing_usage=self.missing_usage,
        )
        return canonical(value.model_dump(mode="json"))


class Rows:
    def __init__(self, root: DatabaseRoot, actor: Actor) -> None:
        root.check_loop()
        self.root, self.actor = root, copy_actor(actor)
        self.owner = actor.id.encode()

    def table(self, name: str) -> sql.Identifier:
        return sql.Identifier(self.root.schema, name)

    async def head(self, connection: Connection, *, write: bool) -> tuple[int, int]:
        if write:
            await connection.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,0,0) ON CONFLICT DO NOTHING").format(
                    self.table("owner_heads")
                ),
                (self.owner,),
            )
        cursor = await connection.execute(
            sql.SQL("SELECT seq,floor FROM {} WHERE owner=%s FOR {}").format(
                self.table("owner_heads"), sql.SQL("UPDATE" if write else "SHARE")
            ),
            (self.owner,),
        )
        row = await cursor.fetchone()
        if row is None:
            return 0, 0
        sequence, floor = row
        if not isinstance(sequence, int) or not isinstance(floor, int):
            raise IntegrityError("owner_head_integrity")
        if floor < 0 or sequence < floor:
            raise IntegrityError("owner_head_integrity")
        return sequence, floor

    async def clock(self, connection: Connection) -> datetime:
        cursor = await connection.execute("SELECT clock_timestamp()")
        row = await cursor.fetchone()
        if row is None or not isinstance(row[0], datetime):
            raise IntegrityError("database_clock_integrity")
        return row[0].astimezone(UTC)

    async def frame(self, connection: Connection, session_id: str, *, write: bool) -> Frame:
        cursor = await connection.execute(
            sql.SQL(
                "SELECT CASE WHEN octet_length(summary)+octet_length(projection)<=%s "
                "THEN summary END, CASE WHEN octet_length(summary)+octet_length(projection)<=%s "
                "THEN projection END, entry_count,created_at,updated_at,status, "
                "ref,title_fold,tags, "
                "generation,lease_turn,lease_until FROM {} WHERE owner=%s AND id=%s FOR {}"
            ).format(self.table("sessions"), sql.SQL("UPDATE" if write else "SHARE")),
            (
                self.root.limits.max_batch_bytes,
                self.root.limits.max_batch_bytes,
                self.owner,
                session_id.encode(),
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise UnknownSession("unknown_session")
        return self.decode(session_id, row)

    def decode(self, session_id: str, row: tuple[object, ...]) -> Frame:
        if not isinstance(row[0], bytes) or not isinstance(row[1], bytes):
            raise StoreError("projection_read_bytes")
        try:
            summary = SessionSummary.model_validate_json(row[0])
            stored = StoredProjection.model_validate_json(row[1])
        except ValueError:
            raise IntegrityError("projection_integrity") from None
        expected = (
            summary.entry_count,
            summary.created_at,
            summary.updated_at,
            summary.status,
            summary.ref.encode() if summary.ref is not None else None,
            (summary.title or "").casefold().encode(),
            [tag.encode() for tag in summary.tags],
        )
        if row[2:9] != expected or summary.id != session_id or summary.owner != self.actor.id:
            raise IntegrityError("projection_integrity")
        expected_state = new_projection(
            session_id, self.actor.id, stored.init, summary.created_at
        ).state
        if (
            stored.state != expected_state
            or stored.state.id != session_id
            or stored.state.owner != self.actor.id
            or stored.init.ref != summary.ref
            or stored.init.title != summary.title
            or stored.init.tags != summary.tags
            or summary.usage_complete != (stored.missing_usage == 0)
        ):
            raise IntegrityError("projection_integrity")
        generation, turn, until = row[9:]
        if not isinstance(generation, int) or generation < 0:
            raise IntegrityError("lease_integrity")
        if (turn is not None and not isinstance(turn, bytes)) or (
            until is not None and not isinstance(until, datetime)
        ):
            raise IntegrityError("lease_integrity")
        if (turn is None) != (until is None):
            raise IntegrityError("lease_integrity")
        return Frame(
            Projection(stored.state, stored.init, summary, stored.specs),
            stored.missing_usage,
            generation,
            turn.decode() if isinstance(turn, bytes) else None,
            until,
            len(row[0]) + len(row[1]),
        )

    async def create(self, init: SessionInit, session_id: str) -> SessionSummary:
        init = init.model_copy(deep=True)

        async def create(connection: Connection) -> SessionSummary:
            await self.head(connection, write=True)
            if init.ref is not None:
                cursor = await connection.execute(
                    sql.SQL("SELECT id FROM {} WHERE owner=%s AND ref=%s").format(
                        self.table("sessions")
                    ),
                    (self.owner, init.ref.encode()),
                )
                row = await cursor.fetchone()
                if row is not None and isinstance(row[0], bytes):
                    frame = await self.frame(connection, row[0].decode(), write=False)
                    return frame.projection.summary
            frame = Frame(
                new_projection(session_id, self.actor.id, init, await self.clock(connection))
            )
            await self.insert(connection, frame)
            await self.publish(connection, frame, ())
            return frame.projection.summary

        return await self.root.run(self.actor, session_id, create, write=True)

    async def insert(self, connection: Connection, frame: Frame) -> None:
        summary = frame.projection.summary
        content = frame.content()
        summary_bytes = canonical(summary.model_dump(mode="json"))
        if len(content) + len(summary_bytes) > self.root.limits.max_batch_bytes:
            raise StoreError("projection_write_bytes")
        try:
            await connection.execute(
                sql.SQL(
                    "INSERT INTO {} (owner,id,ref,summary,projection,created_at,updated_at,status,"
                    "title_fold,tags,entry_count,generation) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)"
                ).format(self.table("sessions")),
                (
                    self.owner,
                    summary.id.encode(),
                    summary.ref.encode() if summary.ref is not None else None,
                    summary_bytes,
                    content,
                    summary.created_at,
                    summary.updated_at,
                    summary.status,
                    (summary.title or "").casefold().encode(),
                    [tag.encode() for tag in summary.tags],
                    summary.entry_count,
                ),
            )
        except UniqueViolation:
            raise EntryConflict("session_identity_conflict") from None
        frame.bytes_used = len(summary_bytes) + len(content)

    async def publish(
        self, connection: Connection, frame: Frame, entries: tuple["Entry", ...]
    ) -> None:
        cursor = await connection.execute(
            sql.SQL("UPDATE {} SET seq=seq+1 WHERE owner=%s RETURNING seq").format(
                self.table("owner_heads")
            ),
            (self.owner,),
        )
        row = await cursor.fetchone()
        if row is None or not isinstance(row[0], int):
            raise IntegrityError("owner_head_integrity")
        seq = row[0]
        projection = frame.projection
        projection.summary = projection.summary.model_copy(update={"last_seq": seq})
        summary = projection.summary
        change = PersistedChange(
            seq=seq, session_id=summary.id, kind="updated", summary=summary, entries=entries
        )
        content = canonical(change.model_dump(mode="json"))
        if len(content) + len(frame.content()) > self.root.limits.max_batch_bytes:
            raise StoreError("journal_write_bytes")
        updated = await connection.execute(
            sql.SQL(
                "UPDATE {} SET summary=%s,projection=%s,updated_at=%s,status=%s,title_fold=%s,"
                "tags=%s,entry_count=%s WHERE owner=%s AND id=%s"
            ).format(self.table("sessions")),
            (
                canonical(summary.model_dump(mode="json")),
                frame.content(),
                summary.updated_at,
                summary.status,
                (summary.title or "").casefold().encode(),
                [tag.encode() for tag in summary.tags],
                summary.entry_count,
                self.owner,
                summary.id.encode(),
            ),
        )
        if updated.rowcount != 1:
            raise IntegrityError("projection_publish_missing")
        frame.bytes_used = len(canonical(summary.model_dump(mode="json"))) + len(frame.content())
        await connection.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s)").format(self.table("changes")),
            (self.owner, seq, summary.id.encode(), content),
        )
        floor = max(0, seq - self.root.limits.feed_retention)
        await connection.execute(
            sql.SQL("DELETE FROM {} WHERE owner=%s AND seq<=%s").format(self.table("changes")),
            (self.owner, floor),
        )
        await connection.execute(
            sql.SQL("UPDATE {} SET floor=GREATEST(floor,%s) WHERE owner=%s").format(
                self.table("owner_heads")
            ),
            (floor, self.owner),
        )

    async def view(self, connection: Connection, frame: Frame) -> SessionView:
        sid = frame.projection.state.id
        maximum = self.root.limits.max_batch_bytes - frame.bytes_used
        cursor = await connection.execute(
            sql.SQL(
                "SELECT p.invocation, "
                "CASE WHEN octet_length(e.envelope)+COALESCE(octet_length(s.envelope),0)<=%s "
                "THEN e.envelope END, "
                "CASE WHEN octet_length(e.envelope)+COALESCE(octet_length(s.envelope),0)<=%s "
                "THEN s.envelope END, "
                "octet_length(e.envelope)+COALESCE(octet_length(s.envelope),0) "
                "FROM {} p JOIN {} e ON e.owner=p.owner AND e.session=p.session "
                "AND e.position=p.position "
                "LEFT JOIN {} i ON i.owner=p.owner AND i.session=p.session "
                "AND i.invocation=p.invocation "
                "LEFT JOIN {} s ON s.owner=i.owner AND s.session=i.session "
                "AND s.position=i.position "
                "WHERE p.owner=%s AND p.session=%s ORDER BY p.first_position LIMIT 1"
            ).format(
                self.table("pending"),
                self.table("entries"),
                self.table("sources"),
                self.table("entries"),
            ),
            (maximum, maximum, self.owner, sid.encode()),
        )
        row = await cursor.fetchone()
        pending = None
        if row is not None:
            if not isinstance(row[3], int) or row[3] > maximum or not isinstance(row[1], bytes):
                raise StoreError("projection_read_bytes")
            if not isinstance(row[0], bytes):
                raise IntegrityError("pending_integrity")
            try:
                requested = Entry.model_validate_json(row[1])
                source = Entry.model_validate_json(row[2]) if isinstance(row[2], bytes) else None
                pending = pending_confirmation(sid, row[0].decode(), requested, source)
            except ValueError:
                raise IntegrityError("pending_integrity") from None
        if bool(pending) != frame.projection.summary.pending:
            raise IntegrityError("pending_integrity")
        projection = frame.projection
        return SessionView(
            summary=projection.summary,
            settings=projection.init.settings,
            specs=projection.specs,
            pending=pending,
        )
