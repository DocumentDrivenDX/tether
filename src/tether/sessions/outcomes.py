"""Reconstruct bounded accepted turn outcomes from journal facts and original sources."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from tether.engine import Engine
from tether.types import Message
from tether.types.client import ReadLimit
from tether.types.execution import Actor, Entry, ExecutionDenied, TurnResult
from tether.types.storage import IntegrityError, TurnInProgress

from .access import SessionAccess
from .content import pending_view


def terminal(entries: Sequence[Entry]) -> Entry:
    for entry in reversed(entries):
        if entry.kind == "turn_ended":
            return entry
        if entry.kind in {"turn_started", "turn_resumed"}:
            raise TurnInProgress("turn_in_progress")
    raise TurnInProgress("turn_in_progress")


async def read_outcome(
    access: SessionAccess, engine: Engine, actor: Actor, session_id: str, turn_id: str
) -> TurnResult:
    return await access.metadata(
        actor, session_id, lambda: outcome(access, engine, actor, session_id, turn_id)
    )


async def outcome(
    access: SessionAccess, engine: Engine, actor: Actor, session_id: str, turn_id: str
) -> TurnResult:
    entries = await access.store.lookup(
        session_id,
        actor.id,
        kind="turn",
        value=turn_id,
        limit=access.limits.max_lookup_entries,
        max_bytes=access.limits.max_read_bytes,
    )
    ended = terminal(entries)
    stop, reason = ended.facts.get("stop_reason"), ended.facts.get("reason")
    if not isinstance(stop, str) or (reason is not None and not isinstance(reason, str)):
        raise IntegrityError("turn_outcome_integrity")
    if stop == "cancelled":
        raise asyncio.CancelledError
    messages: list[Message] = []
    used = 0
    if stop in {"final", "confirmation_required"}:
        for entry in entries:
            if entry.kind != "message" or entry.history_ref is None:
                continue
            value = await access.hydrate(actor, session_id, entry.history_ref)
            if value.status != "ready" or value.record is None or value.record.message is None:
                raise ExecutionDenied("result_history_unavailable")
            message = value.record.message
            used += access.size(message)
            if used > access.limits.max_read_bytes:
                raise ReadLimit("read_bytes")
            messages.append(message)
    pending = await pending_view(access, engine, actor, session_id)
    return TurnResult(
        turn_id=turn_id,
        stop_reason=stop,
        reason=reason,
        messages=tuple(messages),
        pending=(pending,)
        if pending is not None and any(e.key == "pending:" + pending.invocation_id for e in entries)
        else (),
    )
