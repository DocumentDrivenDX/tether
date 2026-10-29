"""Bounded preparation under accepted input, current read policy and actual ownership."""

import asyncio
from functools import partial

from tether.types.canonical import canonical, digest
from tether.types.execution import (
    CommitUnknown,
    EntryConflict,
    ExecutionDenied,
    TurnResult,
    TurnStart,
    copy_actor,
)
from tether.types.history import HistoryRecord
from tether.types.preparation import Preparation, PreparationRequest, PreparationResult
from tether.types.storage import StoreError

from .governance import Governance
from .workers import Workers


async def prepare(
    gov: Governance, start: TurnStart, participant: Preparation, workers: Workers
) -> TurnResult | None:
    source = next(e for e in reversed(gov.log.entries) if e.kind == "message")
    if source.history_ref is None:
        raise ExecutionDenied("preparation_source_missing")
    accepted = next(
        e for e in gov.log.entries if e.kind == "turn_started" and e.turn_id == gov.turn_id
    )
    request = PreparationRequest(
        actor=copy_actor(gov.actor),
        session_id=gov.log.session.id,
        turn_id=gov.turn_id,
        request_id=start.request_id,
        fingerprint=str(accepted.facts["input_digest"]),
        source_index=source.index,
        source=source.history_ref,
        attachments=start.attachments,
    )
    before = canonical(request.model_dump(mode="json"))
    await gov.fact("preparation", key="preparing:" + gov.turn_id, state="waiting")
    try:
        await gov.read(source.history_ref, source_index=source.index)
        async with asyncio.timeout(gov.limits.hook_timeout_seconds):
            value = await workers.call(gov.log.session.id, participant.prepare, request)
        result = PreparationResult.model_validate(value.model_dump(mode="json"))
        if canonical(request.model_dump(mode="json")) != before:
            raise ExecutionDenied("preparation_input_changed")
        if len(canonical(result.model_dump(mode="json"))) > gov.limits.max_result_bytes:
            raise ExecutionDenied("preparation_result_bytes")
        await gov.read(source.history_ref, source_index=source.index)
        await verify_references(gov, workers, result)
    except asyncio.CancelledError:
        await asyncio.shield(gov.fact("turn_ended", stop_reason="cancelled"))
        raise
    except (CommitUnknown, EntryConflict, StoreError):
        raise
    except Exception:
        result = PreparationResult(state="refused", reason="preparation_unavailable")
    await gov.fact("preparation", key="prepared:" + gov.turn_id, **result.model_dump(mode="json"))
    if result.state == "ready":
        return None
    await gov.fact("turn_ended", stop_reason="preparation_" + result.state, reason=result.reason)
    return TurnResult(
        turn_id=gov.turn_id, stop_reason="preparation_" + result.state, reason=result.reason
    )


async def verify_references(gov: Governance, workers: Workers, result: PreparationResult) -> None:
    used = 0
    for ref in result.references:
        if (ref.owner, ref.session_id, ref.kind) != (
            gov.actor.id,
            gov.log.session.id,
            "message",
        ):
            raise ExecutionDenied("preparation_reference_scope")
        await gov.authorize("retrieve", ref.key)
        message = await workers.call(
            gov.log.session.id,
            partial(
                gov.history.read,
                ref,
                actor=copy_actor(gov.actor),
                session_id=gov.log.session.id,
            ),
        )
        record = (
            HistoryRecord(kind="message", message=message).model_dump(mode="json")
            if ref.codec == "tether-history-v1"
            else message.model_dump(mode="json")
        )
        used += len(canonical(record))
        if digest(record) != ref.sha256 or used > gov.limits.max_result_bytes:
            raise ExecutionDenied("preparation_reference_invalid")
        await gov.authorize("retrieve", ref.key)
