"""Manual decisions settle evidence; they never execute or manufacture success."""

from collections.abc import Sequence

from tether.types import JsonValue, Message
from tether.types.canonical import digest
from tether.types.execution import (
    Entry,
    ExecutionDenied,
    HistoryRef,
    NewEntry,
    NotUnresolved,
    Resolution,
    ResolutionConflict,
)

from .effects import unresolved_invocations
from .governance import Governance
from .results import result_content_key


def prior_resolution(entries: Sequence[Entry], value: Resolution) -> bool:
    prior = next((e for e in entries if e.key == "resolution:" + value.invocation_id), None)
    if prior is None:
        return False
    if prior.kind != "resolved" or prior.facts.get("invocation_id") != value.invocation_id:
        raise ExecutionDenied("resolution_integrity")
    if prior.facts.get("outcome") != value.outcome or prior.facts.get("reason") != value.reason:
        raise ResolutionConflict("resolution_conflict")
    return True


async def authorize(gov: Governance, invocation_id: str) -> list[NewEntry]:
    decisions: list[NewEntry] = []
    for action, resource in (
        ("read_session", gov.log.session.id),
        ("resolve_effect", invocation_id),
    ):
        entry = await gov.decision(action, resource)
        if not entry.facts["allowed"]:
            await gov.commit([*decisions, entry])
            raise ExecutionDenied(str(entry.facts["reason"]))
        decisions.append(entry)
    return decisions


def bound_source(gov: Governance, invocation_id: str, intent: Entry) -> Entry:
    source = next(
        (
            s
            for s in gov.log.entries
            for m in s.call_manifest
            if digest([gov.log.session.id, s.index, m.position]) == invocation_id
            and m.call_id == intent.facts.get("call_id")
            and (m.tool, m.tool_digest, m.effect, m.replay, s.turn_id)
            == (
                intent.facts.get("tool"),
                intent.facts.get("tool_digest"),
                "effectful",
                intent.facts.get("replay"),
                intent.turn_id,
            )
        ),
        None,
    )
    if source is None:
        raise ExecutionDenied("invocation_source_missing")
    return source


async def existing_ref(
    gov: Governance,
    intent: Entry,
    invocation_id: str,
    message: Entry | None,
    result: Entry | None,
) -> HistoryRef | None:
    entries = [e for e in (message, result) if e is not None]
    if not entries:
        return None
    refs = [e.history_ref for e in entries]
    ref = refs[0]
    if (
        ref is None
        or any(r != ref for r in refs)
        or (ref.owner, ref.session_id, ref.kind) != (gov.actor.id, gov.log.session.id, "message")
    ):
        raise ExecutionDenied("resolution_history_integrity")
    if message is not None and (
        message.kind,
        message.facts.get("role"),
        message.facts.get("tool_call_id"),
        message.facts.get("is_error"),
        message.turn_id,
    ) != ("message", "tool", intent.facts.get("call_id"), True, intent.turn_id):
        raise ExecutionDenied("resolution_history_integrity")
    if result is not None and (
        result.kind,
        result.facts.get("invocation_id"),
        result.facts.get("tool"),
        result.turn_id,
    ) != ("tool_call", invocation_id, intent.facts.get("tool"), intent.turn_id):
        raise ExecutionDenied("resolution_history_integrity")
    if len(entries) == 1:
        payload = await gov.read(ref)
        if (payload.role, payload.tool_call_id, payload.is_error, payload.tool_calls) != (
            "tool",
            intent.facts.get("call_id"),
            True,
            (),
        ):
            raise ExecutionDenied("resolution_history_integrity")
    return ref


async def unknown_result(gov: Governance, invocation_id: str, intent: Entry) -> list[NewEntry]:
    bound_source(gov, invocation_id, intent)
    keyed = {e.key: e for e in gov.log.entries}
    message_key, result_key = "message:result:" + invocation_id, "result:" + invocation_id
    ref = await existing_ref(
        gov, intent, invocation_id, keyed.get(message_key), keyed.get(result_key)
    )
    if message_key in keyed and result_key in keyed:
        return []
    message = Message(
        role="tool",
        content="outcome_unknown",
        is_error=True,
        tool_call_id=str(intent.facts["call_id"]),
    )
    if ref is None:
        ref = await gov.history.put(
            actor=gov.actor,
            session_id=gov.log.session.id,
            key=result_content_key(invocation_id, message),
            message=message,
        )
    entries: list[NewEntry] = []
    if message_key not in keyed:
        entries.append(
            NewEntry(
                kind="message",
                key=message_key,
                turn_id=intent.turn_id,
                history_ref=ref,
                facts={"role": "tool", "tool_call_id": message.tool_call_id, "is_error": True},
            )
        )
    if result_key not in keyed:
        entries.append(
            NewEntry(
                kind="tool_call",
                key=result_key,
                turn_id=intent.turn_id,
                history_ref=ref,
                facts={
                    "invocation_id": invocation_id,
                    "tool": intent.facts["tool"],
                    "status": "interrupted",
                    "reason": "outcome_unknown",
                    "effect": "effectful",
                    "replay": intent.facts.get("replay"),
                },
            )
        )
    return entries


async def resolve(gov: Governance, value: Resolution) -> None:
    decisions = await authorize(gov, value.invocation_id)
    if prior_resolution(gov.log.entries, value):
        return
    intent = unresolved_invocations(gov.log.entries).get(value.invocation_id)
    if intent is None:
        raise NotUnresolved("not_unresolved")
    closures = await unknown_result(gov, value.invocation_id, intent)
    decisions = await authorize(gov, value.invocation_id)
    facts: dict[str, JsonValue] = {
        "invocation_id": value.invocation_id,
        "outcome": value.outcome,
        "by": gov.actor.id,
        "reason": value.reason,
    }
    await gov.commit(
        [
            *decisions,
            *closures,
            NewEntry(
                kind="resolved",
                key="resolution:" + value.invocation_id,
                turn_id=intent.turn_id,
                facts=facts,
            ),
            NewEntry(kind="x.effects.resolved", facts=facts),
        ]
    )
