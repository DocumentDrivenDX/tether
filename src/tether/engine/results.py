"""Immutable content addresses stay distinct from the single journal result key."""

from tether.types import Message
from tether.types.canonical import digest
from tether.types.execution import CallManifest, ExecutionDenied, NewEntry

from .governance import Governance


def result_content_key(invocation_id: str, message: Message) -> str:
    return "result:" + invocation_id + ":" + digest(message.model_dump(mode="json"))


async def prepare_result(
    gov: Governance,
    invocation_id: str,
    manifest: CallManifest,
    status: str,
    reason: str | None,
    message: Message | None = None,
) -> tuple[Message, list[NewEntry]]:
    message = message or Message(
        role="tool", content=reason or status, tool_call_id=manifest.call_id, is_error=True
    )
    ref = await gov.history.put(
        actor=gov.actor,
        session_id=gov.log.session.id,
        key=result_content_key(invocation_id, message),
        message=message,
    )
    sources = [
        e
        for e in gov.log.entries
        for m in e.call_manifest
        if digest([gov.log.session.id, e.index, m.position]) == invocation_id and m == manifest
    ]
    if len(sources) != 1:
        raise ExecutionDenied("invocation_source_missing")
    source = sources[0]
    return message, [
        NewEntry(
            kind="tool_result" if source.kind == "tool_command" else "message",
            key="message:result:" + invocation_id,
            turn_id=source.turn_id,
            history_ref=ref,
            facts={
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "is_error": message.is_error,
            },
        ),
        NewEntry(
            kind="tool_call",
            key="result:" + invocation_id,
            turn_id=source.turn_id,
            history_ref=ref,
            facts={
                "invocation_id": invocation_id,
                "tool": manifest.tool,
                "status": status,
                "reason": reason,
                "effect": manifest.effect,
                "replay": manifest.replay,
            },
        ),
    ]
