"""Explicit maintenance acquisition identity with separately scoped row ownership."""

from datetime import datetime

from psycopg import sql

from tether.types.canonical import canonical
from tether.types.execution import Actor, copy_actor
from tether.types.storage import IntegrityError, RecoveredTurn, StoreError

from .leases import Leases
from .root import DatabaseRoot
from .rows import Connection


class Recovery:
    def __init__(self, root: DatabaseRoot, actor: Actor, *, owner: str | None = None) -> None:
        root.check_open()
        self.root, self.actor, self.owner = root, copy_actor(actor), owner

    async def candidates(
        self, connection: Connection, now: datetime
    ) -> tuple[tuple[str, str], ...]:
        maximum = self.root.limits.page_limit
        where = sql.SQL("") if self.owner is None else sql.SQL(" AND owner=%s")
        params = () if self.owner is None else (self.owner.encode(),)
        cursor = await connection.execute(
            sql.SQL(
                "WITH selected AS (SELECT owner,id FROM {} WHERE "
                "(lease_until<=%s OR (lease_until IS NULL AND status='running')) {} "
                "ORDER BY owner,id LIMIT %s), bounded AS (SELECT *,"
                "row_number() OVER (ORDER BY owner,id) AS ordinal, "
                "sum(octet_length(owner)+octet_length(id)) OVER (ORDER BY owner,id) AS size "
                "FROM selected) SELECT CASE WHEN size<=%s THEN owner END, "
                "CASE WHEN size<=%s THEN id END FROM bounded WHERE ordinal<=COALESCE("
                "(SELECT min(ordinal) FROM bounded WHERE size>%s),%s) ORDER BY ordinal"
            ).format(sql.Identifier(self.root.schema, "sessions"), where),
            (now, *params, maximum + 1, *([self.root.limits.max_batch_bytes] * 3), maximum + 1),
        )
        rows = await cursor.fetchall()
        if len(rows) > maximum:
            raise StoreError("recovery_candidate_count")
        result: list[tuple[str, str]] = []
        for owner, sid in rows:
            if not isinstance(owner, bytes) or not isinstance(sid, bytes):
                raise StoreError("recovery_candidate_bytes")
            try:
                result.append((owner.decode(), sid.decode()))
            except UnicodeDecodeError:
                raise IntegrityError("recovery_candidate_integrity") from None
        return tuple(result)

    async def recover(self, *, now: datetime) -> tuple[RecoveredTurn, ...]:
        if now.tzinfo is None:
            raise ValueError("invalid_recovery_datetime")

        async def recover(connection: Connection) -> tuple[RecoveredTurn, ...]:
            clock = Leases(self.root, self.actor)
            at = min(now, await clock.clock(connection))
            candidates = await self.candidates(connection, at)
            values: list[RecoveredTurn] = []
            used = 0
            for owner, sid in candidates:
                scoped = Leases(self.root, self.actor, owner=owner)
                recovered = await scoped.recover_locked(connection, sid, at)
                if recovered is not None:
                    used += len(canonical(recovered.model_dump(mode="json")))
                    if used > self.root.limits.max_batch_bytes:
                        raise StoreError("recovery_result_bytes")
                    values.append(recovered)
            return tuple(values)

        return await self.root.run(self.actor, None, recover, write=True)
