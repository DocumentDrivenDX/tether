"""Known journal decisions precede independent participant actions and survive restart."""

import asyncio
from collections.abc import Sequence
from functools import partial

from tether.types import JsonValue
from tether.types.canonical import digest
from tether.types.execution import (
    CommitUnknown,
    EntryConflict,
    ExecutionDenied,
    NewEntry,
    SettlementStage,
    copy_actor,
)
from tether.types.settlement import (
    SettlementBinding,
    SettlementLog,
    SettlementOutcome,
    SettlementParticipant,
)
from tether.types.storage import StoreError

from .governance import Governance
from .workers import Workers


def status_entry(gov: Governance, binding: SettlementBinding, status: str) -> NewEntry:
    return NewEntry(
        kind="settlement",
        key="settlement:" + binding.settlement_id + ":" + status,
        turn_id=gov.turn_id,
        facts={
            "invocation_id": binding.invocation_id,
            "binding": binding.model_dump(mode="json"),
            "status": status,
        },
    )


async def finish(
    gov: Governance,
    workers: Workers,
    stage: SettlementStage,
    invocation_id: str,
    entries: Sequence[NewEntry],
    *,
    withheld: bool,
) -> bool:
    if not entries or entries[-1].facts.get("effect") != "effectful":
        raise ExecutionDenied("settlement_requires_effectful_tool")
    participant = gov.settlements.get(stage.participant)
    if participant is None:
        raise ExecutionDenied("settlement_participant_unavailable")
    result_digest = digest([entry.model_dump(mode="json") for entry in entries])
    intent = next(
        (entry for entry in gov.log.entries if entry.key == "intent:" + invocation_id), None
    )
    if intent is None or not isinstance(intent.facts.get("spec_digest"), str):
        raise ExecutionDenied("settlement_intent_missing")
    fields: dict[str, JsonValue] = dict(
        invocation_id=invocation_id,
        participant=stage.participant,
        stage_id=stage.stage_id,
        owner=gov.log.session.owner,
        session_id=gov.log.session.id,
        actor_id=gov.actor.id,
        principal=gov.actor.principal or gov.actor.id,
        spec_digest=intent.facts["spec_digest"],
        result_digest=result_digest,
        decision="rollback" if withheld else "commit",
    )
    binding = SettlementBinding.model_validate(fields | {"settlement_id": digest(fields)})
    pending = status_entry(gov, binding, "pending")
    pending = pending.model_copy(
        update={
            "facts": pending.facts
            | {
                "result_entries": [entry.model_dump(mode="json") for entry in entries],
                "profile": participant.profile,
            }
        }
    )
    if participant.profile == "shared":
        await shared(gov, entries, pending, binding, participant, withheld=withheld)
        return True
    await gov.commit([pending])
    outcome: SettlementOutcome = "uncertain"
    try:
        async with asyncio.timeout(gov.limits.hook_timeout_seconds):
            outcome = await workers.call(
                gov.log.session.id, participant.settle, binding, copy_actor(gov.actor)
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        outcome = "uncertain"
    expected = "rolled_back" if withheld else "committed"
    if outcome != expected:
        await gov.commit([status_entry(gov, binding, "uncertain")])
        return False
    await gov.commit([*entries, status_entry(gov, binding, expected)])
    return True


async def reconcile(gov: Governance, workers: Workers, settlement_id: str) -> None:
    await gov.authorize("resolve_effect", settlement_id)
    pending = next(
        (e for e in gov.log.entries if e.key == "settlement:" + settlement_id + ":pending"), None
    )
    if pending is None:
        raise ExecutionDenied("settlement_unavailable")
    binding = SettlementBinding.model_validate(pending.facts.get("binding"))
    if (binding.actor_id, binding.owner, binding.session_id, binding.principal) != (
        gov.actor.id,
        gov.log.session.owner,
        gov.log.session.id,
        gov.actor.principal or gov.actor.id,
    ):
        raise ExecutionDenied("settlement_actor_changed")
    expected = "committed" if binding.decision == "commit" else "rolled_back"
    if any(e.key == "settlement:" + settlement_id + ":" + expected for e in gov.log.entries):
        return
    participant = gov.settlements.get(binding.participant)
    if participant is None or participant.profile != "independent":
        raise ExecutionDenied("settlement_participant_unavailable")
    async with asyncio.timeout(gov.limits.hook_timeout_seconds):
        outcome = await workers.call(
            gov.log.session.id, participant.reconcile, binding, copy_actor(gov.actor)
        )
    if outcome != expected:
        raise ExecutionDenied("settlement_uncertain")
    raw = pending.facts.get("result_entries")
    if not isinstance(raw, list):
        raise ExecutionDenied("settlement_result_invalid")
    entries = tuple(NewEntry.model_validate(value) for value in raw)
    if digest([entry.model_dump(mode="json") for entry in entries]) != binding.result_digest:
        raise ExecutionDenied("settlement_result_invalid")
    await gov.commit([*entries, status_entry(gov, binding, expected)])


async def shared(
    gov: Governance,
    entries: Sequence[NewEntry],
    pending: NewEntry,
    binding: SettlementBinding,
    participant: SettlementParticipant,
    *,
    withheld: bool,
) -> None:
    if not isinstance(gov.log, SettlementLog):
        raise ExecutionDenied("settlement_shared_unsupported")
    expected = "rolled_back" if withheld else "committed"
    # The adapter publishes decision, application change and result under one fence.
    try:
        await gov.commit(
            [*entries, pending, status_entry(gov, binding, expected)],
            append=partial(gov.log.settle, binding=binding, participant=participant),
        )
    except (CommitUnknown, EntryConflict, StoreError, ExecutionDenied):
        raise
    except Exception:
        raise ExecutionDenied("settlement_failed") from None
