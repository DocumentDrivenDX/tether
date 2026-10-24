"""Read-only pending validation shared by inspection and deferred reconstruction."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime

from tether.types import Message
from tether.types.canonical import digest
from tether.types.execution import (
    Actor,
    AgentSpec,
    ApprovalBinding,
    CallManifest,
    Entry,
    ExecutionDenied,
    ExecutionLimits,
    PendingConfirmation,
    SessionState,
    Tool,
)
from tether.types.tooling import Normalized, definition, normalize

from .tools import Registry
from .workers import Workers


def normalize_pending(
    proposal: Message,
    manifest: CallManifest,
    binding: ApprovalBinding,
    tool: Tool,
    spec: AgentSpec,
    actor: Actor,
    session_id: str,
    maximum: int,
) -> Normalized:
    if not 0 <= manifest.position < len(proposal.tool_calls):
        raise ExecutionDenied("approval_source_missing")
    call = proposal.tool_calls[manifest.position]
    if (
        call.id != manifest.call_id
        or call.name != manifest.tool
        or digest(call.arguments) != manifest.args_digest
        or digest(definition(tool)) != binding.tool_digest
        or binding.tool_digest != manifest.tool_digest
    ):
        raise ExecutionDenied("approval_invalid")
    args = normalize(tool, call.arguments, maximum)
    if (
        args.sha256 != binding.args_digest
        or binding.actor_id != actor.id
        or binding.session_id != session_id
        or binding.spec_digest != digest(spec.model_dump(mode="json"))
        or digest(definition(tool)) != binding.tool_digest
    ):
        raise ExecutionDenied("approval_invalid")
    return args


async def describe_pending(
    actor: Actor,
    session: SessionState,
    source: Entry,
    request: Entry,
    proposal: Message,
    registry: Registry,
    specs: Mapping[str, AgentSpec],
    workers: Workers,
    limits: ExecutionLimits,
) -> PendingConfirmation:
    binding = ApprovalBinding.model_validate(request.facts.get("binding"))
    spec = specs.get(binding.spec_digest)
    manifest = next(
        (
            m
            for m in source.call_manifest
            if digest([session.id, source.index, m.position]) == binding.invocation_id
        ),
        None,
    )
    if (
        actor.id != session.owner
        or spec is None
        or manifest is None
        or request.facts.get("state") != "requested"
        or manifest.tool not in registry.tools
    ):
        raise ExecutionDenied("approval_invalid")
    tool = registry.tools[manifest.tool].tool
    async with asyncio.timeout(limits.hook_timeout_seconds):
        args = await workers.call(
            session.id,
            normalize_pending,
            proposal,
            manifest,
            binding,
            tool,
            spec,
            actor,
            session.id,
            limits.max_argument_bytes,
        )
    expiry = request.facts.get("expires_at")
    return PendingConfirmation(
        **binding.model_dump(),
        tool=manifest.tool,
        arguments=args.arguments,
        requested_at=request.at,
        history_ref=source.history_ref,
        description=tool.description,
        expires_at=datetime.fromisoformat(expiry) if isinstance(expiry, str) else None,
    )
