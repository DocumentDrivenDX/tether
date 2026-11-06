"""Atomic receipts retain original positions, including entries without journal keys."""

from collections.abc import Sequence
from datetime import datetime

from psycopg import sql

from tether.types.canonical import canonical
from tether.types.execution import Entry, NewEntry
from tether.types.storage import IntegrityError, StoreError

from .journal import Journal
from .row_codec import entry as decode_entry
from .rows import Connection, Frame


async def replay(
    store: Journal,
    connection: Connection,
    frame: Frame,
    values: Sequence[NewEntry],
    marker: NewEntry,
    maximum: int,
) -> tuple[Entry, ...]:
    positions = marker.facts.get("entry_positions")
    if not isinstance(positions, list) or len(positions) != len(values):
        raise IntegrityError("history_operation_receipt")
    accepted: list[Entry] = []
    for value, position in zip(values, positions, strict=True):
        if type(position) is not int or not 0 <= position < frame.projection.summary.entry_count:
            raise IntegrityError("history_operation_receipt")
        cursor = await connection.execute(
            sql.SQL(
                "SELECT CASE WHEN octet_length(envelope)<=%s THEN envelope END,position "
                "FROM {} WHERE owner=%s AND session=%s AND position=%s"
            ).format(store.table("entries")),
            (maximum, store.owner, frame.projection.state.id.encode(), position),
        )
        row = await cursor.fetchone()
        if row is None or not isinstance(row[0], bytes):
            raise StoreError("history_operation_read")
        entry = decode_entry(row[0], row[1])
        if NewEntry.model_validate(entry.model_dump(exclude={"index", "at"})) != value:
            raise IntegrityError("history_operation_receipt")
        maximum -= len(row[0])
        accepted.append(entry)
    return tuple(accepted)


async def append_batch(
    store: Journal,
    connection: Connection,
    frame: Frame,
    values: Sequence[NewEntry],
    marker: NewEntry | None,
    at: datetime,
    *,
    maximum: int,
) -> tuple[tuple[Entry, ...], tuple[Entry, ...]]:
    if marker is None:
        return await store.append(connection, frame, values, at)
    if "entry_positions" in marker.facts:
        receipt, new_receipt = await store.append(connection, frame, [marker], at)
        if new_receipt:
            raise IntegrityError("history_operation_receipt")
        remaining = maximum - len(canonical(receipt[0].model_dump(mode="json")))
        accepted = await replay(store, connection, frame, values, marker, remaining)
        inserted = ()
    else:
        accepted, inserted = await store.append(connection, frame, values, at)
        marker = marker.model_copy(
            update={"facts": marker.facts | {"entry_positions": [e.index for e in accepted]}}
        )
        receipt, new_receipt = await store.append(connection, frame, [marker], at)
    if any(e.index >= receipt[0].index for e in accepted):
        raise IntegrityError("history_operation_receipt")
    result = (*accepted, *receipt)
    if sum(len(canonical(e.model_dump(mode="json"))) for e in result) > maximum:
        raise StoreError("history_operation_bytes")
    return result, (*inserted, *new_receipt)
