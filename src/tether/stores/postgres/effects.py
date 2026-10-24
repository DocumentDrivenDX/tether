"""Indexed unresolved-effect metadata; original intent facts remain authoritative."""

from itertools import pairwise

from psycopg import sql

from tether.types.execution import Entry
from tether.types.storage import EffectInfo, EffectPage, IntegrityError

from .listing import Listing
from .rows import Connection


def info(entry: Entry) -> EffectInfo:
    facts = entry.facts
    invocation, tool = facts.get("invocation_id"), facts.get("tool")
    if (
        entry.kind != "tool_call"
        or facts.get("status") != "started"
        or facts.get("effect") != "effectful"
        or not isinstance(invocation, str)
        or not isinstance(tool, str)
    ):
        raise IntegrityError("effect_integrity")
    return EffectInfo(
        invocation_id=invocation,
        tool=tool,
        intent_index=entry.index,
        reason=str(facts.get("reason") or "outcome_unknown"),
    )


class Effects(Listing):
    async def effects(self, session_id: str, *, start: int = 0, limit: int = 50) -> EffectPage:
        self.limit(limit)
        if start < 0:
            raise ValueError("invalid_page")

        async def read(connection: Connection) -> EffectPage:
            count = await self.scope(connection, session_id)
            entries, following = await self.entry_rows(
                connection,
                sql.SQL(
                    "SELECT e.position,e.envelope FROM {} i JOIN {} e "
                    "ON e.owner=i.owner AND e.session=i.session AND e.position=i.position "
                    "WHERE i.owner=%s AND i.session=%s AND i.position>=%s "
                    "ORDER BY e.position LIMIT %s"
                ).format(self.table("effects"), self.table("entries")),
                (self.owner, session_id.encode(), start, limit + 1),
                limit=limit,
                budget=self.root.limits.max_batch_bytes,
            )
            positions = tuple(entry.index for entry in entries)
            if following is not None:
                positions += (following,)
            if any(left >= right for left, right in pairwise(positions)):
                raise IntegrityError("effect_integrity")
            values = tuple(info(entry) for entry in entries)
            if any(value.intent_index >= count for value in values) or (
                following is not None and following >= count
            ):
                raise IntegrityError("effect_integrity")
            await self.effect_membership(connection, session_id, values)
            return EffectPage(items=values, next_start=following)

        return await self.root.run(self.actor, session_id, read)

    async def effect_membership(
        self,
        connection: Connection,
        session_id: str,
        values: tuple[EffectInfo, ...],
    ) -> None:
        if len({value.invocation_id for value in values}) != len(values):
            raise IntegrityError("effect_integrity")
        for start in range(0, len(values), 128):
            batch = values[start : start + 128]
            cursor = await connection.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE owner=%s AND session=%s AND ({})").format(
                    self.table("effects"),
                    sql.SQL(" OR ").join([sql.SQL("(invocation=%s AND position=%s)")] * len(batch)),
                ),
                (
                    self.owner,
                    session_id.encode(),
                    *(
                        v
                        for value in batch
                        for v in (value.invocation_id.encode(), value.intent_index)
                    ),
                ),
            )
            if await cursor.fetchone() != (len(batch),):
                raise IntegrityError("effect_integrity")
