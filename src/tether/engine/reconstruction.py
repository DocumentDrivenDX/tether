"""Rebuild executable runner inputs from accepted facts and current authorized sources."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime

from tether.types import Message, TokenUsage
from tether.types.canonical import digest
from tether.types.execution import (
    AgentSpec,
    ApprovalBinding,
    CallManifest,
    Entry,
    ExecutionDenied,
    HistoryRef,
    PendingConfirmation,
    UsageSoFar,
)

from .inspection import normalize_pending
from .run import Run


def pending_bindings(run: Run) -> dict[str, tuple[ApprovalBinding, datetime, datetime | None]]:
    result: dict[str, tuple[ApprovalBinding, datetime, datetime | None]] = {}
    for entry in run.gov.log.entries:
        if entry.kind != "confirmation":
            continue
        binding = ApprovalBinding.model_validate(entry.facts.get("binding"))
        if entry.facts.get("state") != "requested":
            result.pop(binding.invocation_id, None)
            continue
        raw_expiry = entry.facts.get("expires_at")
        expiry = datetime.fromisoformat(raw_expiry) if isinstance(raw_expiry, str) else None
        result[binding.invocation_id] = (binding, entry.at, expiry)
    return result


def pinned_spec(entries: Sequence[Entry], specs: Mapping[str, AgentSpec]) -> AgentSpec:
    for entry in reversed(entries):
        if entry.kind == "turn_started":
            sha = entry.facts.get("spec_digest")
            if isinstance(sha, str) and sha in specs:
                spec = specs[sha]
                if digest(spec.model_dump(mode="json")) == sha:
                    return spec.model_copy(deep=True)
            raise ExecutionDenied("spec_unavailable")
    raise ExecutionDenied("spec_unavailable")


async def restore_pending(run: Run, invocation_id: str) -> bool:
    bindings = pending_bindings(run)
    if invocation_id not in bindings:
        return False
    await run.gov.authorize("read_session", run.gov.log.session.id)
    for source in reversed(run.gov.log.entries):
        ids = {
            digest([run.gov.log.session.id, source.index, m.position]) for m in source.call_manifest
        }
        if invocation_id not in ids:
            continue
        run.executor.bind(source)
        proposal = await run.gov.read(source.history_ref)
        completed = {e.key for e in run.gov.log.entries}
        deferred: set[str] = set()
        for manifest in source.call_manifest:
            inv_id = digest([run.gov.log.session.id, source.index, manifest.position])
            if "result:" + inv_id in completed:
                continue
            deferred.add(manifest.call_id)
            if inv_id not in bindings:
                continue
            binding, requested, expiry = bindings[inv_id]
            run.executor.pending[inv_id] = await _pending(
                run, proposal, manifest, binding, requested, expiry, source.history_ref
            )
        run.deferred_ids = frozenset(deferred)
        return True
    raise ExecutionDenied("approval_source_missing")


async def _pending(
    run: Run,
    proposal: Message,
    manifest: CallManifest,
    binding: ApprovalBinding,
    requested: datetime,
    expiry: datetime | None,
    source_ref: HistoryRef | None,
) -> PendingConfirmation:
    pinned = run.executor.registry.tools.get(manifest.tool)
    if pinned is None:
        raise ExecutionDenied("approval_invalid")
    async with asyncio.timeout(run.gov.limits.hook_timeout_seconds):
        args = await run.executor.workers.call(
            run.gov.log.session.id,
            normalize_pending,
            proposal,
            manifest,
            binding,
            pinned.tool,
            run.executor.spec,
            run.gov.actor,
            run.gov.log.session.id,
            run.gov.limits.max_argument_bytes,
        )
    return PendingConfirmation(
        **binding.model_dump(),
        tool=manifest.tool,
        arguments=args.arguments,
        history_ref=source_ref,
        description=pinned.tool.description,
        requested_at=requested,
        expires_at=expiry,
    )


def requested_turn(entries: Sequence[Entry], invocation_id: str) -> str | None:
    requested: str | None = None
    for entry in entries:
        if entry.kind != "confirmation":
            continue
        binding = entry.facts.get("binding")
        if not isinstance(binding, dict) or binding.get("invocation_id") != invocation_id:
            continue
        requested = entry.turn_id if entry.facts.get("state") == "requested" else None
    return requested


def restored_usage(entries: Sequence[Entry], turn_id: str) -> UsageSoFar:
    calls = [e for e in entries if e.kind == "model_call" and e.turn_id == turn_id]
    usages = [
        TokenUsage.model_validate(e.facts["usage"])
        for e in calls
        if e.facts.get("usage") is not None
    ]
    complete = len(usages) == len(calls) and all(_single_attempt(e) for e in calls)
    return UsageSoFar(
        steps=sum(e.facts.get("purpose", "answer") == "answer" for e in calls),
        model_calls=sum(e.facts.get("purpose", "answer") != "summary" for e in calls),
        summary_calls=sum(e.facts.get("purpose") == "summary" for e in calls),
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        usage_complete=complete,
    )


def _single_attempt(entry: Entry) -> bool:
    ids = entry.facts.get("attempt_ids")
    return ids is None or (isinstance(ids, list) and len(ids) <= 1)
