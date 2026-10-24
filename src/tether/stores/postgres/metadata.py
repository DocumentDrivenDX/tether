"""Compare cached columns without returning untrusted variable-width metadata."""

from collections.abc import Sequence

from psycopg import AsyncConnection, sql

from tether.types.storage import IntegrityError, SessionSummary

CONDITION = sql.SQL(
    "(id=%s AND entry_count=%s AND created_at=%s AND updated_at=%s AND status=%s "
    "AND ref IS NOT DISTINCT FROM %s AND title_fold=%s AND tags=%s::bytea[])"
)


def values(summary: SessionSummary) -> tuple[object, ...]:
    return (
        summary.id.encode(),
        summary.entry_count,
        summary.created_at,
        summary.updated_at,
        summary.status,
        summary.ref.encode() if summary.ref is not None else None,
        (summary.title or "").casefold().encode(),
        [tag.encode() for tag in summary.tags],
    )


async def verify(
    connection: AsyncConnection[tuple[object, ...]],
    table: sql.Identifier,
    owner: bytes,
    summaries: Sequence[SessionSummary],
) -> None:
    # Bounded parameter groups remain below the protocol limit for any configured page size.
    for start in range(0, len(summaries), 128):
        batch = summaries[start : start + 128]
        cursor = await connection.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE owner=%s AND ({})").format(
                table,
                sql.SQL(" OR ").join([CONDITION] * len(batch)),
            ),
            (owner, *(value for summary in batch for value in values(summary))),
        )
        if await cursor.fetchone() != (len(batch),):
            raise IntegrityError("projection_integrity")
