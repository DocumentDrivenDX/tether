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
    current = await _entries(access, actor, session_id, turn_id)
    entries = await _chain_entries(access, engine, actor, session_id, current)
    ended = terminal(current)
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
        if stop == "confirmation_required"
        and pending is not None
        and any(_pending_entry(e, pending.invocation_id) for e in current)
        else (),
    )


async def _entries(access: SessionAccess, actor: Actor, sid: str, tid: str) -> tuple[Entry, ...]:
    return await access.store.lookup(
        sid,
        actor.id,
        kind="turn",
        value=tid,
        limit=access.limits.max_lookup_entries,
        max_bytes=access.limits.max_read_bytes,
    )


async def _chain_entries(
    access: SessionAccess,
    engine: Engine,
    actor: Actor,
    sid: str,
    current: tuple[Entry, ...],
) -> tuple[Entry, ...]:
    result = list(current)
    starts = [e for e in current if e.kind == "turn_started"]
    seen = {e.turn_id for e in starts}
    used = sum(access.size(e) for e in current)
    while starts and starts[0].facts.get("resumes") is not None:
        parent = starts[0].facts["resumes"]
        if not isinstance(parent, str) or parent in seen or len(seen) > engine.limits.max_resumes:
            raise IntegrityError("resume_chain_integrity")
        seen.add(parent)
        entries = await _entries(access, actor, sid, parent)
        if terminal(entries).facts.get("stop_reason") != "interrupted":
            raise IntegrityError("resume_chain_integrity")
        used += sum(access.size(e) for e in entries)
        result.extend(entries)
        if used > access.limits.max_read_bytes or len(result) > access.limits.max_lookup_entries:
            raise ReadLimit("read_bytes")
        starts = [e for e in entries if e.kind == "turn_started"]
        if len(starts) != 1:
            raise IntegrityError("resume_chain_integrity")
    return tuple(sorted(result, key=lambda e: e.index))


def _pending_entry(entry: Entry, invocation_id: str) -> bool:
    binding = entry.facts.get("binding")
    return (
        entry.kind == "confirmation"
        and entry.facts.get("state") == "requested"
        and isinstance(binding, dict)
        and binding.get("invocation_id") == invocation_id
    )
