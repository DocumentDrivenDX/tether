"""Owner-scoped metadata keyset queries; no transcript or history hydration."""

from datetime import UTC, datetime

from psycopg import sql

from tether.stores.queries import decode_cursor, encode_cursor, matches, position, query_digest
from tether.types.execution import ExecutionDenied
from tether.types.storage import IntegrityError, Page, SessionQuery, SessionSummary, StoreError

from . import row_codec
from .metadata import verify
from .reads import Reads
from .rows import Connection


def criteria(query: SessionQuery, cursor: str | None) -> tuple[sql.Composed, tuple[object, ...]]:
    terms: list[sql.Composable] = [sql.SQL("owner=%s")]
    params: list[object] = [query.owner.encode()]
    if query.ref is not None:
        terms.append(sql.SQL("ref=%s"))
        params.append(query.ref.encode())
    if query.status is not None:
        terms.append(sql.SQL("status=ANY(%s::text[])"))
        params.append(
            sorted(query.status & {"idle", "running", "waiting", "interrupted", "closed"})
        )
    if query.tags_all:
        terms.append(sql.SQL("tags @> %s::bytea[]"))
        params.append([tag.encode() for tag in query.tags_all])
    for date, operator in (
        (query.updated_after, sql.SQL(">")),
        (query.updated_before, sql.SQL("<")),
    ):
        if date is not None:
            if date.tzinfo is None:
                raise ValueError("invalid_query_datetime")
            terms.append(sql.SQL("updated_at {} %s").format(operator))
            params.append(date)
    if query.text is not None:
        terms.append(sql.SQL("position(%s::bytea in title_fold)>0"))
        params.append(query.text.casefold().encode())
    if cursor is not None:
        terms.append(after(query, cursor, params))
    return sql.SQL(" AND ").join(terms), tuple(params)


def after(query: SessionQuery, cursor: str, params: list[object]) -> sql.Composed:
    date, identity = decode_cursor(cursor, query_digest(query))
    try:
        parsed = datetime.fromisoformat(date)
        if parsed.tzinfo is None or parsed.astimezone(UTC).isoformat() != date:
            raise ValueError("cursor_date")
    except ValueError:
        raise ValueError("invalid_cursor") from None
    params.extend((parsed, identity.encode()))
    return sql.SQL("({},id) {} (%s,%s)").format(
        sql.Identifier("updated_at" if query.order == "updated_desc" else "created_at"),
        sql.SQL(">" if query.order == "created_asc" else "<"),
    )


def selection(table: sql.Identifier, query: SessionQuery, where: sql.Composed) -> sql.Composed:
    order = sql.SQL("ASC" if query.order == "created_asc" else "DESC")
    column = sql.Identifier("updated_at" if query.order == "updated_desc" else "created_at")
    return sql.SQL(
        "WITH selected AS (SELECT id,summary,updated_at,created_at FROM {} WHERE {} "
        "ORDER BY {} {},id {} LIMIT %s), bounded AS (SELECT *, "
        "row_number() OVER (ORDER BY {} {},id {}) AS ordinal, "
        "sum(octet_length(id)+octet_length(summary)) OVER (ORDER BY {} {},id {}) AS size "
        "FROM selected) SELECT CASE WHEN ordinal<=%s AND size<=%s THEN id END, "
        "CASE WHEN ordinal<=%s AND size<=%s THEN summary END,ordinal "
        "FROM bounded ORDER BY ordinal"
    ).format(table, where, column, order, order, column, order, order, column, order, order)


class Listing(Reads):
    async def list(
        self,
        query: SessionQuery,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[SessionSummary]:
        self.limit(limit)
        query = query.model_copy(deep=True)
        if query.owner != self.actor.id:
            raise ExecutionDenied("denied")
        where, params = criteria(query, cursor)
        maximum = self.root.limits.max_batch_bytes

        async def read(connection: Connection) -> Page[SessionSummary]:
            sequence, _ = await self.head(connection, write=False)
            if sequence == 0:
                return Page(items=())
            result = await connection.execute(
                selection(self.table("sessions"), query, where),
                (*params, limit + 1, limit, maximum, limit, maximum),
            )
            rows = await result.fetchall()
            values: list[SessionSummary] = []
            for row in rows[:limit]:
                if not isinstance(row[0], bytes) or not isinstance(row[1], bytes):
                    raise StoreError("list_read_bytes")
                value = row_codec.summary(row[1], row[0], self.actor.id)
                if value.last_seq > sequence or not matches(value, query):
                    raise IntegrityError("projection_integrity")
                values.append(value)
            await verify(connection, self.table("sessions"), self.owner, values)
            following = (
                encode_cursor(query_digest(query), position(values[-1], query))
                if len(rows) > limit
                else None
            )
            return Page(items=tuple(values), next=following)

        return await self.root.run(self.actor, None, read)

    async def find(self, ref: str) -> SessionSummary | None:
        page = await self.list(SessionQuery(owner=self.actor.id, ref=ref), limit=1)
        return page.items[0] if page.items else None
