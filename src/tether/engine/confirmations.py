"""Prepare informational approval content before the atomic decision/resume commit."""

from __future__ import annotations

from tether.types import Message
from tether.types.canonical import canonical, digest
from tether.types.execution import (
    ApprovalBinding,
    ExecutionDenied,
    HistoryRef,
    NewEntry,
    PendingConfirmation,
)

from .governance import Governance


async def approval_entries(
    gov: Governance, pending: PendingConfirmation, state: str, note: str | None, *, resuming: bool
) -> list[NewEntry]:
    ref: HistoryRef | None = None
    if note is not None:
        message = Message(role="user", content=note)
        encoded = canonical(message.model_dump(mode="json"))
        if len(encoded) > gov.limits.max_argument_bytes:
            raise ExecutionDenied("approval_note_limit")
        ref = await gov.history.put(
            actor=gov.actor,
            session_id=gov.log.session.id,
            key="approval-note:"
            + pending.invocation_id
            + ":"
            + digest(message.model_dump(mode="json")),
            message=message,
        )
    binding = pending.model_dump(include=set(ApprovalBinding.model_fields), mode="json")
    entries = [
        NewEntry(
            kind="confirmation",
            key="approval:" + pending.invocation_id,
            turn_id=gov.turn_id,
            history_ref=ref,
            facts={
                "state": state,
                "binding": binding,
                "note_kind": "approval_note" if ref else None,
            },
        )
    ]
    if resuming:
        entries.append(
            NewEntry(
                kind="turn_resumed",
                turn_id=gov.turn_id,
                facts={"purpose": "confirmation", "invocation_id": pending.invocation_id},
            )
        )
    return entries
