"""Independent original, shown and executable argument bindings survive approval/restart."""

import asyncio
from copy import deepcopy
from typing import cast

from tether.types import JsonValue, Message, ToolCall
from tether.types.approval_edits import ApprovalEditRequest
from tether.types.canonical import canonical, digest
from tether.types.execution import (
    AgentSpec,
    CallManifest,
    Entry,
    ExecutionDenied,
    NewEntry,
    PendingConfirmation,
    Tool,
    copy_actor,
)
from tether.types.tooling import Normalized, definition, normalize

from .governance import Governance
from .workers import Workers


async def transform(
    gov: Governance,
    workers: Workers,
    tool: Tool,
    invocation_id: str,
    original: dict[str, JsonValue],
    shown: dict[str, JsonValue],
) -> Normalized:
    participant = gov.transforms.get(tool.name)
    if participant is None or not participant.id or not participant.version:
        raise ExecutionDenied("approval_edit_unsupported")
    request = ApprovalEditRequest(
        actor=copy_actor(gov.actor),
        session_id=gov.log.session.id,
        invocation_id=invocation_id,
        original=deepcopy(original),
        shown=deepcopy(shown),
    )
    before = canonical(request.model_dump(mode="json"))
    try:
        async with asyncio.timeout(gov.limits.hook_timeout_seconds):
            value = cast(
                dict[str, JsonValue],
                await workers.call(gov.log.session.id, participant.transform, request),
            )
            call = ToolCall(id=invocation_id, name=tool.name, arguments=value)
            normalized = await workers.call(
                gov.log.session.id, normalize, tool, call.arguments, gov.limits.max_argument_bytes
            )
        if canonical(request.model_dump(mode="json")) != before:
            raise ExecutionDenied("approval_edit_mutated")
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ExecutionDenied("approval_edit_invalid") from None
    return normalized


async def prepare(
    gov: Governance,
    workers: Workers,
    pending: PendingConfirmation,
    shown: dict[str, JsonValue],
    tool: Tool,
    spec: AgentSpec,
) -> tuple[PendingConfirmation, list[NewEntry]]:
    if any(entry.key == "intent:" + pending.invocation_id for entry in gov.log.entries):
        raise ExecutionDenied("approval_edit_after_intent")
    if (
        digest(definition(tool)) != pending.tool_digest
        or digest(spec.model_dump(mode="json")) != pending.spec_digest
    ):
        raise ExecutionDenied("approval_invalid")
    source = next(
        (
            entry
            for entry in gov.log.entries
            for manifest in entry.call_manifest
            if digest([gov.log.session.id, entry.index, manifest.position]) == pending.invocation_id
        ),
        None,
    )
    if source is None:
        raise ExecutionDenied("approval_source_missing")
    manifest = next(
        value
        for value in source.call_manifest
        if digest([gov.log.session.id, source.index, value.position]) == pending.invocation_id
    )
    original = (await gov.read(source.history_ref, source_index=source.index)).tool_calls[
        manifest.position
    ]
    normalized = await transform(
        gov, workers, tool, pending.invocation_id, original.arguments, shown
    )
    participant = gov.transforms[tool.name]
    entries: list[NewEntry] = []
    for kind, arguments in (("approval_shown", shown), ("approval_edit", normalized.arguments)):
        ref = await gov.history.put(
            actor=gov.actor,
            session_id=gov.log.session.id,
            key=kind + ":" + pending.invocation_id + ":" + digest(arguments),
            message=Message(
                role="user",
                content="",
                tool_calls=(original.model_copy(update={"arguments": arguments}, deep=True),),
            ),
        )
        entries.append(
            NewEntry(
                kind=kind,
                key=kind + ":" + pending.invocation_id,
                turn_id=source.turn_id,
                history_ref=ref,
                facts={
                    "invocation_id": pending.invocation_id,
                    "proposal_digest": manifest.args_digest,
                    "shown_digest": digest(shown),
                    "executable_digest": normalized.sha256,
                    "tool_digest": pending.tool_digest,
                    "spec_digest": pending.spec_digest,
                    "transform_id": participant.id,
                    "transform_version": participant.version,
                },
            )
        )
    return pending.model_copy(
        update={
            "arguments": normalized.arguments,
            "args_digest": normalized.sha256,
            "proposal_digest": manifest.args_digest,
            "shown_digest": digest(shown),
            "executable_digest": normalized.sha256,
        },
        deep=True,
    ), entries


async def executable(
    gov: Governance,
    workers: Workers,
    tool: Tool,
    invocation_id: str,
    source: Entry,
    manifest: CallManifest,
    original: dict[str, JsonValue],
) -> Normalized | None:
    edit = next(
        (entry for entry in gov.log.entries if entry.key == "approval_edit:" + invocation_id), None
    )
    if edit is None:
        return None
    shown = next(
        (entry for entry in gov.log.entries if entry.key == "approval_shown:" + invocation_id), None
    )
    participant = gov.transforms.get(tool.name)
    if (
        shown is None
        or participant is None
        or (edit.facts.get("transform_id"), edit.facts.get("transform_version"))
        != (participant.id, participant.version)
    ):
        raise ExecutionDenied("approval_edit_invalid")
    shown_call = (await gov.read(shown.history_ref, source_index=shown.index)).tool_calls[0]
    executable_call = (await gov.read(edit.history_ref, source_index=edit.index)).tool_calls[0]
    normalized = await transform(gov, workers, tool, invocation_id, original, shown_call.arguments)
    if (
        edit.facts.get("proposal_digest"),
        edit.facts.get("shown_digest"),
        edit.facts.get("executable_digest"),
        executable_call.arguments,
    ) != (
        manifest.args_digest,
        digest(shown_call.arguments),
        normalized.sha256,
        normalized.arguments,
    ):
        raise ExecutionDenied("approval_edit_changed")
    return normalized
