"""Internal transactional entry/index/projection primitives under owner/session locks."""

from collections.abc import Sequence
from datetime import datetime

from psycopg import sql

from tether.stores.indexing import entry_groups, ledger_change
from tether.stores.projection import finish_summary, new_projection
from tether.stores.projections import SessionIndex, create_index
from tether.stores.usage import add_usage, usage_change, usage_observations
from tether.types import TokenUsage
from tether.types.canonical import canonical, digest
from tether.types.execution import (
    Entry,
    EntryConflict,
    ExecutionDenied,
    NewEntry,
    WorkerOutstanding,
)
from tether.types.storage import (
    IntegrityError,
    SessionArchive,
    SessionSummary,
    StoreError,
    TurnInProgress,
    UnknownSession,
)

from .row_codec import entry as decode_entry
from .rows import Connection, Frame, Rows


class Journal(Rows):
    async def append(
        self,
        connection: Connection,
        frame: Frame,
        values: Sequence[NewEntry],
        at: datetime,
    ) -> tuple[tuple[Entry, ...], tuple[Entry, ...]]:
        accepted: list[Entry] = []
        inserted: list[Entry] = []
        used = 0
        for value in values:
            old = await self.existing(
                connection,
                frame.projection.state.id,
                value.key,
                self.root.limits.max_batch_bytes - used,
            )
            if old is not None:
                if NewEntry.model_validate(old.model_dump(exclude={"index", "at"})) != value:
                    raise EntryConflict("entry_conflict")
                used += len(canonical(old.model_dump(mode="json")))
                accepted.append(old)
                continue
            entry = Entry(**value.model_dump(), index=frame.projection.summary.entry_count, at=at)
            used += len(canonical(entry.model_dump(mode="json")))
            if used > self.root.limits.max_batch_bytes:
                raise StoreError("journal_write_bytes")
            await self.apply(connection, frame, entry)
            accepted.append(entry)
            inserted.append(entry)
        return tuple(accepted), tuple(inserted)

    async def existing(
        self, connection: Connection, session_id: str, key: str | None, budget: int
    ) -> Entry | None:
        if key is None:
            return None
        cursor = await connection.execute(
            sql.SQL(
                "SELECT CASE WHEN octet_length(envelope)<=%s THEN envelope END,position "
                "FROM {} WHERE owner=%s AND session=%s AND entry_key=%s"
            ).format(self.table("entries")),
            (budget, self.owner, session_id.encode(), key.encode()),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if not isinstance(row[0], bytes):
            raise StoreError("entry_read_bytes")
        entry = decode_entry(row[0], row[1])
        if entry.key != key:
            raise IntegrityError("entry_integrity")
        return entry

    async def apply(self, connection: Connection, frame: Frame, entry: Entry) -> None:
        sid = frame.projection.state.id
        if entry.index != frame.projection.summary.entry_count:
            raise IntegrityError("entry_order_invalid")
        await connection.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s)").format(self.table("entries")),
            (
                self.owner,
                sid.encode(),
                entry.index,
                entry.key.encode() if entry.key is not None else None,
                canonical(entry.model_dump(mode="json")),
            ),
        )
        await self.groups(connection, sid, entry)
        await self.ledger(connection, sid, entry)
        frame.projection.metadata(entry)
        if entry.kind in {"model_call", "model_attempt", "model_receipt"}:
            await self.usage(connection, frame, entry)
        counts: list[int] = []
        for name in ("pending", "effects"):
            cursor = await connection.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE owner=%s AND session=%s").format(
                    self.table(name)
                ),
                (self.owner, sid.encode()),
            )
            row = await cursor.fetchone()
            if row is None or not isinstance(row[0], int):
                raise IntegrityError("projection_count_integrity")
            counts.append(row[0])
        frame.projection.summary = finish_summary(
            frame.projection.summary, entry, pending=counts[0], effects=counts[1]
        )

    async def groups(self, connection: Connection, session_id: str, entry: Entry) -> None:
        groups = entry_groups(session_id, entry)
        if groups:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s)").format(
                        self.table("entry_groups")
                    ),
                    [
                        (self.owner, session_id.encode(), kind, value.encode(), entry.index)
                        for kind, value in groups
                    ],
                )
        for manifest in entry.call_manifest:
            invocation = digest([session_id, entry.index, manifest.position])
            await connection.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING").format(
                    self.table("sources")
                ),
                (self.owner, session_id.encode(), invocation.encode(), entry.index),
            )

    async def ledger(self, connection: Connection, session_id: str, entry: Entry) -> None:
        change = ledger_change(entry)
        if change is None:
            return
        name, invocation, present = change
        address = (self.owner, session_id.encode(), invocation.encode())
        if not present:
            await connection.execute(
                sql.SQL("DELETE FROM {} WHERE owner=%s AND session=%s AND invocation=%s").format(
                    self.table(name)
                ),
                address,
            )
        elif name == "pending":
            await connection.execute(
                sql.SQL(
                    "INSERT INTO {} VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (owner,session,invocation) "
                    "DO UPDATE SET position=EXCLUDED.position"
                ).format(self.table(name)),
                (*address, entry.index, entry.index),
            )
        else:
            await connection.execute(
                sql.SQL(
                    "INSERT INTO {} VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (owner,session,invocation) "
                    "DO UPDATE SET position=EXCLUDED.position"
                ).format(self.table(name)),
                (*address, entry.index),
            )

    async def usage(self, connection: Connection, frame: Frame, entry: Entry) -> None:
        for key, value in usage_observations(entry):
            await self._usage_value(connection, frame, key, value)

    async def _usage_value(
        self, connection: Connection, frame: Frame, key: str, value: TokenUsage | None
    ) -> None:
        address = (self.owner, frame.projection.state.id.encode(), key.encode())
        cursor = await connection.execute(
            sql.SQL(
                "SELECT CASE WHEN octet_length(usage)<=%s THEN usage END, "
                "octet_length(usage) FROM {} WHERE owner=%s AND session=%s AND key=%s"
            ).format(self.table("attempt_usage")),
            (self.root.limits.max_batch_bytes, *address),
        )
        row = await cursor.fetchone()
        if row is not None and row[0] is None and row[1] is not None:
            raise StoreError("usage_read_bytes")
        previous = None
        if row is not None and row[0] is not None:
            if not isinstance(row[0], bytes):
                raise IntegrityError("usage_integrity")
            try:
                previous = TokenUsage.model_validate_json(row[0])
            except ValueError:
                raise IntegrityError("usage_integrity") from None
        change = usage_change(previous, value, exists=row is not None)
        encoded = (
            canonical(change.value.model_dump(mode="json")) if change.value is not None else None
        )
        await connection.execute(
            sql.SQL(
                "INSERT INTO {} VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (owner,session,key) DO UPDATE SET usage=EXCLUDED.usage"
            ).format(self.table("attempt_usage")),
            (*address, encoded),
        )
        frame.missing_usage += change.missing_delta
        if frame.missing_usage < 0:
            raise IntegrityError("usage_integrity")
        summary = frame.projection.summary
        frame.projection.summary = summary.model_copy(
            update={
                "usage": add_usage(summary.usage, change.added),
                "usage_complete": frame.missing_usage == 0,
            }
        )

    async def restore(self, archive: SessionArchive, *, created_at: datetime) -> SessionSummary:
        archive = archive.model_copy(deep=True)
        if archive.state.owner != self.actor.id:
            raise ExecutionDenied("denied")
        if created_at.tzinfo is None:
            raise ValueError("invalid_archive_datetime")

        def prepare() -> SessionIndex:
            encoded = canonical(archive.model_dump(mode="json"))
            if len(encoded) > self.root.limits.max_batch_bytes:
                raise StoreError("journal_write_bytes")
            oracle = create_index(archive.state.id, archive.state.owner, archive.init, created_at)
            for entry in archive.entries:
                oracle.apply(entry)
            if oracle.state != archive.state:
                raise IntegrityError("archive_state_integrity")
            return oracle

        oracle = await self.root.prepare(archive.state.id, prepare)

        async def restore(connection: Connection) -> SessionSummary:
            return await self._restore_locked(connection, archive, oracle)

        return await self.root.run(self.actor, archive.state.id, restore, write=True)

    async def _restore_locked(
        self,
        connection: Connection,
        archive: SessionArchive,
        oracle: SessionIndex,
    ) -> SessionSummary:
        if self.root.workers.busy(archive.state.id):
            raise WorkerOutstanding("worker_outstanding")
        await self.head(connection, write=True)
        try:
            current = await self.frame(connection, archive.state.id, write=True)
        except UnknownSession:
            current = None
        if current is not None:
            await self._same_restore(connection, current, archive, oracle)
            return current.projection.summary
        frame = Frame(
            new_projection(archive.state.id, self.actor.id, archive.init, oracle.summary.created_at)
        )
        try:
            await self.insert(connection, frame)
        except EntryConflict:
            raise EntryConflict("archive_restore_conflict") from None
        for entry in archive.entries:
            await self.apply(connection, frame, entry)
        await self.publish(connection, frame, archive.entries)
        return frame.projection.summary

    async def _same_restore(
        self,
        connection: Connection,
        frame: Frame,
        archive: SessionArchive,
        oracle: "SessionIndex",
    ) -> None:
        if self.root.workers.busy(archive.state.id):
            raise WorkerOutstanding("worker_outstanding")
        if frame.lease_until is not None:
            raise TurnInProgress("turn_in_progress")
        oracle.summary = oracle.summary.model_copy(
            update={"last_seq": frame.projection.summary.last_seq}
        )
        current = await self.view(connection, frame)
        if current != oracle.view():
            raise EntryConflict("archive_restore_conflict")
        budget = await connection.execute(
            sql.SQL(
                "SELECT COALESCE(sum(octet_length(envelope)),0) FROM "
                "(SELECT envelope FROM {} WHERE owner=%s AND session=%s "
                "ORDER BY position LIMIT %s) AS bounded"
            ).format(self.table("entries")),
            (self.owner, archive.state.id.encode(), len(archive.entries) + 1),
        )
        size = await budget.fetchone()
        if (
            size is None
            or not isinstance(size[0], int)
            or size[0] > self.root.limits.max_batch_bytes
        ):
            raise StoreError("entry_read_bytes")
        cursor = await connection.execute(
            sql.SQL(
                "SELECT envelope FROM {} WHERE owner=%s AND session=%s ORDER BY position LIMIT %s"
            ).format(self.table("entries")),
            (self.owner, archive.state.id.encode(), len(archive.entries) + 1),
        )
        rows = await cursor.fetchall()
        if len(rows) != len(archive.entries) or any(
            row[0] != canonical(entry.model_dump(mode="json"))
            for row, entry in zip(rows, archive.entries, strict=False)
        ):
            raise EntryConflict("archive_restore_conflict")
