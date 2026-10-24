"""Internal locked SQL primitives; not a qualified public SessionStore yet."""

from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import AsyncConnection, sql
from psycopg.errors import UniqueViolation

from tether.stores.projection import Projection, new_projection
from tether.types.canonical import canonical
from tether.types.execution import Actor, Entry, EntryConflict, copy_actor
from tether.types.mutations import CreateReceipt, created
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

from .metadata import verify
from .root import DatabaseRoot
from .row_codec import lease_turn, pending
from .row_codec import summary as summary_row

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
    def __init__(self, root: DatabaseRoot, actor: Actor, *, owner: str | None = None) -> None:
        root.check_loop()
        self.root, self.actor = root, copy_actor(actor)
        self.owner_id = actor.id if owner is None else owner
        self.owner = self.owner_id.encode()

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
                "SELECT CASE WHEN size<=%s THEN summary END, "
                "CASE WHEN size<=%s THEN projection END,entry_count,generation, "
                "CASE WHEN size<=%s THEN lease_turn END,lease_until,size FROM "
                "(SELECT *,octet_length(summary)+octet_length(projection)+"
                "COALESCE(octet_length(lease_turn),0) AS size FROM {} "
                "WHERE owner=%s AND id=%s FOR {}) AS locked"
            ).format(self.table("sessions"), sql.SQL("UPDATE" if write else "SHARE")),
            (*([self.root.limits.max_batch_bytes] * 3), self.owner, session_id.encode()),
        )
        row = await cursor.fetchone()
        if row is None:
            raise UnknownSession("unknown_session")
        if not isinstance(row[0], bytes) or not isinstance(row[1], bytes):
            raise StoreError("projection_read_bytes")
        try:
            summary = summary_row(row[0], session_id.encode(), self.owner_id)
        except ValueError:
            raise IntegrityError("projection_integrity") from None
        await self.metadata_matches(connection, summary)
        values = (
            summary.entry_count,
            summary.created_at,
            summary.updated_at,
            summary.status,
            summary.ref.encode() if summary.ref is not None else None,
            (summary.title or "").casefold().encode(),
            [tag.encode() for tag in summary.tags],
        )
        frame = self.decode(session_id, (row[0], row[1], *values, *row[3:6]))
        if row[2] != summary.entry_count or not isinstance(row[6], int):
            raise IntegrityError("projection_integrity")
        frame.bytes_used = row[6]
        return frame

    async def metadata_matches(self, connection: Connection, summary: SessionSummary) -> None:
        await verify(connection, self.table("sessions"), self.owner, (summary,))

    def decode(self, session_id: str, row: tuple[object, ...]) -> Frame:
        if not isinstance(row[0], bytes) or not isinstance(row[1], bytes):
            raise StoreError("projection_read_bytes")
        try:
            summary = summary_row(row[0], session_id.encode(), self.owner_id)
            stored = StoredProjection.model_validate_json(row[1])
        except ValueError:
            raise IntegrityError("projection_integrity") from None
        if (
            canonical(summary.model_dump(mode="json")) != row[0]
            or canonical(stored.model_dump(mode="json")) != row[1]
        ):
            raise IntegrityError("projection_integrity")
        expected = (
            summary.entry_count,
            summary.created_at,
            summary.updated_at,
            summary.status,
            summary.ref.encode() if summary.ref is not None else None,
            (summary.title or "").casefold().encode(),
            [tag.encode() for tag in summary.tags],
        )
        if row[2:9] != expected or summary.id != session_id or summary.owner != self.owner_id:
            raise IntegrityError("projection_integrity")
        expected_state = new_projection(
            session_id, self.owner_id, stored.init, summary.created_at
        ).state
        if (
            stored.state != expected_state
            or stored.state.id != session_id
            or stored.state.owner != self.owner_id
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
            lease_turn(turn if isinstance(turn, bytes) else None),
            until,
            len(row[0]) + len(row[1]),
        )

    async def create(self, init: SessionInit, session_id: str) -> SessionSummary:
        return (await self.create_with_receipt(init, session_id)).summary

    async def create_with_receipt(self, init: SessionInit, session_id: str) -> CreateReceipt:
        init = init.model_copy(deep=True)

        async def create(connection: Connection) -> CreateReceipt:
            await self.head(connection, write=True)
            if init.ref is not None:
                cursor = await connection.execute(
                    sql.SQL(
                        "SELECT CASE WHEN octet_length(id)<=%s THEN id END "
                        "FROM {} WHERE owner=%s AND ref=%s"
                    ).format(self.table("sessions")),
                    (self.root.limits.max_batch_bytes, self.owner, init.ref.encode()),
                )
                row = await cursor.fetchone()
                if row is not None:
                    if not isinstance(row[0], bytes):
                        raise StoreError("session_identity_bytes")
                    try:
                        existing_id = row[0].decode()
                    except UnicodeDecodeError:
                        raise IntegrityError("session_identity_integrity") from None
                    frame = await self.frame(connection, existing_id, write=False)
                    return CreateReceipt(
                        summary=frame.projection.summary.model_copy(deep=True),
                        disposition="existing",
                    )
            frame = Frame(
                new_projection(session_id, self.owner_id, init, await self.clock(connection))
            )
            await self.insert(connection, frame)
            await self.publish(connection, frame, ())
            return created(frame.projection.summary)

        return await self.root.run(self.actor, None, create, write=True)

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
        holder_bytes = len(frame.lease_turn.encode()) if frame.lease_turn is not None else 0
        frame_bytes = (
            len(canonical(summary.model_dump(mode="json"))) + len(frame.content()) + holder_bytes
        )
        if max(frame_bytes, len(content) + len(frame.content())) > self.root.limits.max_batch_bytes:
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
        frame.bytes_used = frame_bytes
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
                "SELECT CASE WHEN octet_length(p.invocation)<=%s THEN p.invocation END, "
                "CASE WHEN octet_length(p.invocation)+octet_length(e.envelope)+"
                "COALESCE(octet_length(s.envelope),0)<=%s "
                "THEN e.envelope END, "
                "CASE WHEN octet_length(p.invocation)+octet_length(e.envelope)+"
                "COALESCE(octet_length(s.envelope),0)<=%s "
                "THEN s.envelope END, "
                "octet_length(p.invocation)+octet_length(e.envelope)+"
                "COALESCE(octet_length(s.envelope),0) "
                ",e.position,s.position FROM {} p JOIN {} e "
                "ON e.owner=p.owner AND e.session=p.session "
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
            (maximum, maximum, maximum, self.owner, sid.encode()),
        )
        row = await cursor.fetchone()
        confirmation = None
        if row is not None:
            if (
                not isinstance(row[3], int)
                or row[3] > maximum
                or not isinstance(row[0], bytes)
                or not isinstance(row[1], bytes)
            ):
                raise StoreError("projection_read_bytes")
            confirmation = pending(row, sid, self.owner_id)
        if bool(confirmation) != frame.projection.summary.pending:
            raise IntegrityError("pending_integrity")
        projection = frame.projection
        return SessionView(
            summary=projection.summary,
            settings=projection.init.settings,
            specs=projection.specs,
            pending=confirmation,
        )
