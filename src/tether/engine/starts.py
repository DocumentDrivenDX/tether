"""Prepare content before atomic supersession/input acceptance, without dispatch."""

from __future__ import annotations

from tether.types import Message
from tether.types.canonical import digest, input_digest
from tether.types.execution import AgentSpec, Entry, ExecutionDenied, NewEntry, TurnStart

from .governance import Governance
from .results import result_content_key


async def interrupted_calls(gov: Governance) -> list[NewEntry]:
    entries = gov.log.entries
    keys = {entry.key for entry in entries}
    result: list[NewEntry] = []
    for source in entries:
        for manifest in source.call_manifest:
            invocation_id = digest([gov.log.session.id, source.index, manifest.position])
            if "result:" + invocation_id in keys:
                continue
            requested = next((e for e in entries if e.key == "pending:" + invocation_id), None)
            if requested is not None and "approval:" + invocation_id not in keys:
                result.append(
                    NewEntry(
                        kind="confirmation",
                        key="approval:" + invocation_id,
                        turn_id=source.turn_id,
                        facts={"state": "superseded", "binding": requested.facts.get("binding")},
                    )
                )
            started = "intent:" + invocation_id in keys
            reason = (
                "outcome_unknown"
                if started and manifest.effect == "effectful"
                else "superseded"
                if requested is not None
                else "interrupted"
            )
            message = Message(
                role="tool", content=reason, tool_call_id=manifest.call_id, is_error=True
            )
            ref = await gov.history.put(
                actor=gov.actor,
                session_id=gov.log.session.id,
                key=result_content_key(invocation_id, message),
                message=message,
            )
            result.extend(
                (
                    NewEntry(
                        kind="message",
                        key="message:result:" + invocation_id,
                        turn_id=source.turn_id,
                        history_ref=ref,
                        facts={"role": "tool", "tool_call_id": manifest.call_id, "is_error": True},
                    ),
                    NewEntry(
                        kind="tool_call",
                        key="result:" + invocation_id,
                        turn_id=source.turn_id,
                        history_ref=ref,
                        facts={
                            "invocation_id": invocation_id,
                            "tool": manifest.tool,
                            "status": "interrupted" if started else "denied",
                            "reason": reason,
                            "effect": manifest.effect,
                            "replay": manifest.replay,
                        },
                    ),
                )
            )
    return result


async def accept_input(
    gov: Governance, spec: AgentSpec, text: str, start: TurnStart
) -> tuple[Entry, ...]:
    await gov.authorize("read_session", gov.log.session.id)
    if any(e.kind == "turn_started" and e.turn_id == start.turn_id for e in gov.log.entries):
        raise ExecutionDenied("turn_already_started")
    if gov.log.turn_id is not None and start.turn_id != gov.log.turn_id:
        raise ExecutionDenied("turn_identity_mismatch")
    ref = await gov.history.put(
        actor=gov.actor,
        session_id=gov.log.session.id,
        key="user:" + gov.turn_id,
        message=Message(role="user", content=text, attachments=start.attachments),
    )
    closures = await interrupted_calls(gov)
    return await gov.commit(
        [
            *closures,
            NewEntry(
                kind="turn_started",
                turn_id=gov.turn_id,
                key="request:" + start.request_id if start.request_id is not None else None,
                facts={
                    "spec_digest": digest(spec.model_dump(mode="json")),
                    "spec_id": spec.id,
                    "spec_version": spec.version,
                    "input_digest": input_digest(text, start.attachments, start.requested_spec),
                },
            ),
            NewEntry(
                kind="message",
                turn_id=gov.turn_id,
                history_ref=ref,
                facts={"role": "user", "is_error": False},
            ),
        ]
    )
