"""Bounded history and physical-attempt reads from committed session addresses."""

from __future__ import annotations

from collections.abc import Sequence

from tether.engine import Engine
from tether.types import AttemptIdentity, ResponseReceipt
from tether.types.canonical import canonical
from tether.types.client import CallDetail, HistoryPage, HistorySlot, ReadLimit, UnknownModelCall
from tether.types.execution import (
    Actor,
    ApprovalBinding,
    Entry,
    ExecutionDenied,
    HistoryRef,
    PendingConfirmation,
    SessionState,
)
from tether.types.history import HistoryStatus, HistoryValue
from tether.types.storage import IntegrityError, Page, UnknownSession

from .access import SessionAccess


async def history_page(
    access: SessionAccess, actor: Actor, session_id: str, start: int, limit: int
) -> HistoryPage:
    page = await access.store.entries(session_id, actor.id, start=start, limit=limit)
    items: list[HistorySlot] = []
    used = 0
    for entry in page.items:
        if entry.history_ref is not None:
            value = await access.hydrate(actor, session_id, entry.history_ref)
            slot = HistorySlot(entry_index=entry.index, value=value)
            used += access.size(slot)
            if used > access.limits.max_read_bytes:
                raise ReadLimit("read_bytes")
            items.append(slot)
    return HistoryPage(items=tuple(items), next_start=page.next_start)


async def call_details(
    access: SessionAccess, actor: Actor, session_id: str, ids: Sequence[str]
) -> Page[CallDetail]:
    access.limit(len(ids), maximum=access.limits.max_call_ids)
    groups: list[tuple[Entry, ...]] = []
    used = 0
    for call_id in ids:
        if len(call_id) > access.limits.max_request_bytes:
            raise ReadLimit("read_limit")
        entries = await access.store.lookup(
            session_id,
            actor.id,
            kind="model_call",
            value=call_id,
            limit=access.limits.max_lookup_entries,
            max_bytes=access.limits.max_read_bytes - used,
        )
        if not entries:
            raise UnknownModelCall("unknown_model_call")
        used += sum(len(canonical(e.model_dump(mode="json"))) for e in entries)
        if used > access.limits.max_read_bytes:
            raise ReadLimit("read_bytes")
        groups.append(entries)
    result: list[CallDetail] = []
    used = 0
    for entries in groups:
        for prepared in entries:
            if prepared.kind == "model_attempt":
                value = await attempt_detail(access, actor, session_id, prepared, entries)
                used += access.size(value)
                if used > access.limits.max_read_bytes:
                    raise ReadLimit("read_bytes")
                result.append(value)
    return Page(items=tuple(result))


async def attempt_detail(
    access: SessionAccess,
    actor: Actor,
    session_id: str,
    prepared: Entry,
    entries: tuple[Entry, ...],
) -> CallDetail:
    identity = AttemptIdentity.model_validate(prepared.facts.get("identity"))
    if identity.session_id != session_id:
        raise IntegrityError("attempt_identity")
    receipts: list[ResponseReceipt] = []
    observations: list[HistoryValue] = []
    used = 0
    for entry in (
        prepared,
        *(
            e
            for e in entries
            if e.kind == "model_receipt"
            and e.facts.get("identity") == prepared.facts.get("identity")
        ),
    ):
        raw = entry.facts.get("receipt")
        if raw is not None:
            receipts.append(ResponseReceipt.model_validate(raw))
        raw_ref = entry.facts.get("history_ref")
        if raw_ref is not None:
            value = await access.hydrate(actor, session_id, HistoryRef.model_validate(raw_ref))
            if raw is not None:
                value = value.model_copy(
                    update={
                        "source_locator": value.source_locator
                        | {"receipt_index": str(len(receipts) - 1)}
                    }
                )
            used += access.size(value)
            if used > access.limits.max_read_bytes:
                raise ReadLimit("read_bytes")
            observations.append(value)
    status = observation_status(observations)
    return CallDetail(
        identity=identity,
        receipt=receipts[-1] if receipts else None,
        receipts=tuple(receipts),
        status=status,
        observations=tuple(observations),
    )


def observation_status(values: list[HistoryValue]) -> HistoryStatus:
    if not values:
        return "missing"
    statuses: set[HistoryStatus] = {v.status for v in values}
    if statuses == {"ready"}:
        return (
            "ready"
            if any(v.record is not None and v.record.response is not None for v in values)
            else "partial"
        )
    return next(iter(statuses)) if len(statuses) == 1 else "partial"


async def pending_view(
    access: SessionAccess, engine: Engine, actor: Actor, session_id: str
) -> PendingConfirmation | None:
    view = await access.store.get(session_id, actor.id)
    if view is None:
        raise UnknownSession("unknown_session")
    if view.pending is None:
        return None
    binding = ApprovalBinding.model_validate(
        view.pending.model_dump(include=set(ApprovalBinding.model_fields))
    )
    entries = await access.store.lookup(
        session_id,
        actor.id,
        kind="invocation",
        value=binding.invocation_id,
        limit=access.limits.max_lookup_entries,
        max_bytes=access.limits.max_read_bytes,
    )
    source = next((e for e in entries if e.call_manifest), None)
    requested = [e for e in entries if e.kind == "confirmation"]
    if not requested or requested[-1].facts.get("state") != "requested":
        return None
    request = requested[-1]
    if source is None or source.history_ref is None:
        raise IntegrityError("pending_source_missing")
    pending = view.pending
    value = await access.hydrate(actor, session_id, source.history_ref)
    if value.status != "ready" or value.record is None or value.record.message is None:
        return pending.model_copy(update={"history_status": value.status, "reason": value.reason})
    state = SessionState.model_validate(
        {"id": session_id, "owner": actor.id}
        | {key: v for key, v in view.settings.items() if key in {"held", "approval"}}
    )
    try:
        result = await engine.inspect_pending(actor, state, source, request, value.record.message)
    except ExecutionDenied as error:
        return pending.model_copy(update={"history_status": "unsupported", "reason": error.reason})
    current = await access.store.get(session_id, actor.id)
    if current is None or current.pending != view.pending:
        return None
    return result
