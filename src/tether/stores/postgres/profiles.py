"""Bounded catalog validation of required logged columns and uniqueness semantics."""

from dataclasses import dataclass
from typing import cast

from psycopg import AsyncConnection

from tether.types.storage import IntegrityError


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[tuple[str, str, bool], ...]
    primary: tuple[str, ...]
    unique: tuple[tuple[str, ...], ...] = ()


async def verify_tables(
    connection: AsyncConnection[tuple[object, ...]],
    schema: str,
    tables: tuple[Table, ...],
) -> None:
    cursor = await connection.execute(
        "SELECT c.relname::text, c.relkind, c.relpersistence, "
        "array_agg(a.attname::text ORDER BY a.attnum), "
        "array_agg(a.atttypid::regtype::text ORDER BY a.attnum), "
        "array_agg(a.attnotnull ORDER BY a.attnum), "
        "COALESCE((SELECT jsonb_agg(jsonb_build_array(co.contype::text, "
        "(SELECT jsonb_agg(aa.attname::text ORDER BY k.position) "
        "FROM unnest(co.conkey) WITH ORDINALITY AS k(number, position) "
        "JOIN pg_catalog.pg_attribute aa ON aa.attrelid=c.oid AND aa.attnum=k.number))) "
        "FROM pg_catalog.pg_constraint co WHERE co.conrelid=c.oid AND co.contype IN ('p','u')), "
        "'[]'::jsonb) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 "
        "AND NOT a.attisdropped WHERE n.nspname=%s AND c.relname=ANY(%s) "
        "GROUP BY c.oid,c.relname,c.relkind,c.relpersistence ORDER BY c.relname",
        (schema, [table.name for table in tables]),
    )
    rows = await cursor.fetchall()
    if len(rows) != len(tables):
        raise IntegrityError("database_schema_mismatch")
    for row, table in zip(rows, sorted(tables, key=lambda item: item.name), strict=True):
        names, types, required = zip(*table.columns, strict=True)
        if row[:6] != (table.name, "r", "p", list(names), list(types), list(required)):
            raise IntegrityError("database_schema_mismatch")
        expected = {("p", table.primary)} | {("u", columns) for columns in table.unique}
        if _constraints(row[6]) != expected:
            raise IntegrityError("database_schema_mismatch")


def _constraints(raw: object) -> set[tuple[str, tuple[str, ...]]]:
    if not isinstance(raw, list):
        raise IntegrityError("database_schema_mismatch")
    result: set[tuple[str, tuple[str, ...]]] = set()
    for item in cast(list[object], raw):
        if not isinstance(item, list):
            raise IntegrityError("database_schema_mismatch")
        pair = cast(list[object], item)
        if len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], list):
            raise IntegrityError("database_schema_mismatch")
        values = cast(list[object], pair[1])
        if not all(isinstance(value, str) for value in values):
            raise IntegrityError("database_schema_mismatch")
        result.add((pair[0], tuple(value for value in values if isinstance(value, str))))
    return result
