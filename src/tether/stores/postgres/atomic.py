"""Stable complete-batch identity and expected frontier inside the existing journal."""

from collections.abc import Sequence

from tether.types import JsonValue
from tether.types.canonical import canonical, digest
from tether.types.execution import EntryConflict, NewEntry
from tether.types.storage import IntegrityError, StoreError

from .journal import Journal
from .paired import references
from .rows import Connection, Frame


def operation_key(entries: Sequence[NewEntry]) -> str:
    keys: list[JsonValue] = [entry.key for entry in entries if entry.key is not None]
    return digest(
        keys
        if keys
        else [
            [
                entry.kind,
                entry.turn_id,
                [[ref.provider, ref.owner, ref.session_id, ref.key] for ref in references(entry)],
            ]
            for entry in entries
        ]
    )


async def receipt(
    store: Journal,
    connection: Connection,
    frame: Frame,
    entries: Sequence[NewEntry],
    *,
    key: str,
    expected_frontier: int | None,
) -> NewEntry:
    if not key or len(key.encode()) > store.root.limits.max_batch_bytes:
        raise StoreError("history_operation_key")
    if expected_frontier is not None and (
        type(expected_frontier) is not int or expected_frontier < 0
    ):
        raise StoreError("history_operation_frontier")
    batch_digest = digest(
        {
            "owner": store.actor.id,
            "session": frame.projection.state.id,
            "principal": store.actor.principal or store.actor.id,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }
    )
    stored_key = "history-batch:" + key
    previous = await store.existing(
        connection, frame.projection.state.id, stored_key, store.root.limits.max_batch_bytes
    )
    if previous is not None:
        if previous.facts.get("version") != 1 or not isinstance(
            previous.facts.get("entry_positions"), list
        ):
            raise IntegrityError("history_operation_receipt")
        if (
            previous.kind != "history_batch"
            or previous.facts.get("batch_digest") != batch_digest
            or (
                expected_frontier is not None
                and previous.facts.get("expected_frontier") != expected_frontier
            )
        ):
            raise EntryConflict("history_batch_conflict")
        return NewEntry.model_validate(previous.model_dump(exclude={"index", "at"}))
    frontier = frame.projection.summary.entry_count
    if expected_frontier is not None and expected_frontier != frontier:
        raise EntryConflict("history_batch_frontier")
    result = NewEntry(
        kind="history_batch",
        key=stored_key,
        turn_id=entries[0].turn_id if entries else None,
        facts={
            "version": 1,
            "batch_digest": batch_digest,
            "expected_frontier": frontier,
            "owner": store.actor.id,
            "session_id": frame.projection.state.id,
        },
    )
    if len(canonical(result.model_dump(mode="json"))) > store.root.limits.max_batch_bytes:
        raise StoreError("history_operation_bytes")
    return result
