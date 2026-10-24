"""Canonical scoped journal rows; provider support is decided during later hydration."""

from tether.stores.indexing import pending_confirmation
from tether.types.canonical import canonical
from tether.types.execution import Entry, PendingConfirmation
from tether.types.storage import IntegrityError, SessionSummary


def entry(data: object, position: object) -> Entry:
    if not isinstance(data, bytes):
        raise IntegrityError("entry_integrity")
    try:
        value = Entry.model_validate_json(data)
    except ValueError:
        raise IntegrityError("entry_integrity") from None
    if (
        value.index != position
        or value.index < 0
        or value.at.tzinfo is None
        or canonical(value.model_dump(mode="json")) != data
    ):
        raise IntegrityError("entry_integrity")
    return value


def pending(row: tuple[object, ...], session_id: str, owner: str) -> PendingConfirmation:
    if not isinstance(row[0], bytes):
        raise IntegrityError("pending_integrity")
    requested = entry(row[1], row[4])
    source = entry(row[2], row[5]) if row[2] is not None else None
    if requested.kind != "confirmation" or requested.facts.get("state") != "requested":
        raise IntegrityError("pending_integrity")
    try:
        invocation = row[0].decode()
        value = pending_confirmation(session_id, invocation, requested, source)
    except ValueError:
        raise IntegrityError("pending_integrity") from None
    reference = value.history_ref
    if (
        reference is None
        or value.invocation_id != invocation
        or value.session_id != session_id
        or value.actor_id != owner
        or reference.owner != owner
        or reference.session_id != session_id
    ):
        raise IntegrityError("pending_integrity")
    return value


def lease_turn(data: bytes | None) -> str | None:
    try:
        return data.decode() if data is not None else None
    except ValueError:
        raise IntegrityError("lease_integrity") from None


def summary(data: object, session_id: bytes, owner: str) -> SessionSummary:
    if not isinstance(data, bytes):
        raise IntegrityError("projection_integrity")
    try:
        value = SessionSummary.model_validate_json(data)
    except ValueError:
        raise IntegrityError("projection_integrity") from None
    counts = (
        value.entry_count,
        value.message_count,
        value.turn_count,
        value.unresolved_effect_count,
    )
    dates = (value.created_at, value.updated_at, value.last_turn_at)
    if (
        value.id.encode() != session_id
        or value.owner != owner
        or value.last_seq <= 0
        or any(count < 0 for count in counts)
        or any(date is not None and date.tzinfo is None for date in dates)
        or canonical(value.model_dump(mode="json")) != data
    ):
        raise IntegrityError("projection_integrity")
    return value
