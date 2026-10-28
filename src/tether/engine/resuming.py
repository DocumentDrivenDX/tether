"""Recovery preparation only; Agent executes every eligible invocation."""

import asyncio
from collections.abc import Mapping

from tether.types import Message
from tether.types.canonical import digest
from tether.types.execution import (
    AgentSpec,
    ApprovalBinding,
    CallManifest,
    Entry,
    ExecutionDenied,
    NewEntry,
    ResumeLimit,
    ResumePlan,
    ResumeStep,
    ResumeTool,
)
from tether.types.tooling import definition, tool_spec

from .governance import Governance
from .inspection import normalize_pending
from .reconstruction import pending_bindings, restore_pending
from .recovery import plan_resume, resume_chain
from .results import prepare_result
from .run import Run
from .tools import PinnedTool, Registry
from .workers import Workers


def _plan(entries: tuple[Entry, ...], tools: Mapping[str, PinnedTool], sid: str) -> ResumePlan:
    declarations = {
        name: ResumeTool(tool_digest=digest(definition(p.tool)), replay=p.tool.replay)
        for name, p in tools.items()
    }
    return plan_resume(entries, declarations, session_id=sid)


async def planned(gov: Governance, registry: Registry, workers: Workers) -> ResumePlan:
    async with asyncio.timeout(gov.limits.hook_timeout_seconds):
        plan = await workers.call(
            gov.log.session.id,
            _plan,
            tuple(gov.log.entries),
            registry.tools.copy(),
            gov.log.session.id,
        )
    if len(resume_chain(gov.log.entries, plan.interrupted_turn_id)) > gov.limits.max_resumes:
        raise ResumeLimit("resume_limit")
    if gov.turn_id in {e.turn_id for e in gov.log.entries if e.kind == "turn_started"}:
        raise ExecutionDenied("turn_already_started")
    return plan


async def decisions(gov: Governance) -> list[NewEntry]:
    entries: list[NewEntry] = []
    for action in ("read_session", "resume"):
        entry = await gov.decision(action, gov.log.session.id)
        if not entry.facts["allowed"]:
            await gov.commit([*entries, entry])
            raise ExecutionDenied(str(entry.facts["reason"]))
        entries.append(entry)
    return entries


def requests(entries: tuple[Entry, ...]) -> dict[str, Entry]:
    pending: dict[str, Entry] = {}
    for entry in entries:
        if entry.kind != "confirmation":
            continue
        binding = ApprovalBinding.model_validate(entry.facts.get("binding"))
        if entry.facts.get("state") == "requested":
            pending[binding.invocation_id] = entry
        else:
            pending.pop(binding.invocation_id, None)
    return pending


async def proposal(gov: Governance, step: ResumeStep) -> tuple[Entry, Message]:
    if step.call_entry_index is None or step.call_position is None or step.invocation_id is None:
        raise ExecutionDenied("resume_source_invalid")
    source = gov.log.entries[step.call_entry_index]
    manifest = next((m for m in source.call_manifest if m.position == step.call_position), None)
    message = await gov.read(source.history_ref, source_index=source.index)
    if manifest is None or not 0 <= step.call_position < len(message.tool_calls):
        raise ExecutionDenied("resume_source_invalid")
    call = message.tool_calls[step.call_position]
    if (call.id, call.name, digest(call.arguments)) != (
        manifest.call_id,
        manifest.tool,
        manifest.args_digest,
    ):
        raise ExecutionDenied("resume_source_invalid")
    return source, message


async def valid_pending(
    gov: Governance,
    request: Entry,
    message: Message,
    manifest: CallManifest,
    pinned: PinnedTool,
    spec: AgentSpec,
    workers: Workers,
) -> bool:
    try:
        async with asyncio.timeout(gov.limits.hook_timeout_seconds):
            await workers.call(
                gov.log.session.id,
                normalize_pending,
                message,
                manifest,
                ApprovalBinding.model_validate(request.facts.get("binding")),
                pinned.tool,
                spec,
                gov.actor,
                gov.log.session.id,
                gov.limits.max_argument_bytes,
            )
    except (ExecutionDenied, ValueError, TypeError):
        return False
    return True


async def prepared(
    gov: Governance, plan: ResumePlan, registry: Registry, spec: AgentSpec, workers: Workers
) -> list[NewEntry]:
    await decisions(gov)
    await gov.messages()
    pending = requests(tuple(gov.log.entries))
    entries: list[NewEntry] = []
    for step in plan.steps:
        if step.kind == "model_step":
            continue
        source, message = await proposal(gov, step)
        manifest = next(m for m in source.call_manifest if m.position == step.call_position)
        inv = step.invocation_id or ""
        pinned = registry.tools.get(manifest.tool)
        invalid = pinned is None
        if not invalid and pinned is not None and inv in pending:
            invalid = not await valid_pending(
                gov, pending[inv], message, manifest, pinned, spec, workers
            )
        if step.kind == "record_interrupted" or invalid:
            request = pending.pop(inv, None)
            if request is not None:
                entries.append(
                    NewEntry(
                        kind="confirmation",
                        key="approval:" + inv,
                        turn_id=gov.turn_id,
                        facts={"state": "invalid", "binding": request.facts["binding"]},
                    )
                )
            _, results = await prepare_result(
                gov,
                inv,
                manifest,
                "interrupted" if step.kind == "record_interrupted" else "denied",
                "outcome_unknown"
                if step.kind == "record_interrupted"
                else "approval_invalid"
                if request is not None
                else "definition_changed",
            )
            entries.extend(results)
    entries.extend(
        NewEntry(
            kind="confirmation",
            turn_id=gov.turn_id,
            key="pending:" + inv + ":resume:" + gov.turn_id,
            facts=request.facts | {"resumes_request_index": request.index},
        )
        for inv, request in pending.items()
    )
    return entries


async def accept(
    gov: Governance, plan: ResumePlan, spec: AgentSpec, registry: Registry, workers: Workers
) -> None:
    entries = await prepared(gov, plan, registry, spec, workers)
    auth = await decisions(gov)
    await gov.commit(
        [
            *auth,
            NewEntry(
                kind="turn_started",
                turn_id=gov.turn_id,
                facts={
                    "resumes": plan.interrupted_turn_id,
                    "spec_digest": digest(spec.model_dump(mode="json")),
                    "spec_id": spec.id,
                    "spec_version": spec.version,
                },
            ),
            NewEntry(
                kind="turn_resumed",
                turn_id=gov.turn_id,
                facts={"resumes": plan.interrupted_turn_id, "purpose": "recovery"},
            ),
            *entries,
        ]
    )


async def restore(run: Run, plan: ResumePlan) -> None:
    chain = frozenset(resume_chain(run.gov.log.entries, run.gov.turn_id))
    run.gov.configure_usage(chain)
    run.steps = run.gov.usage.steps
    run.result_turn_ids = chain
    pending = pending_bindings(run)
    if pending:
        await restore_pending(run, next(iter(pending)))
    completed = {e.key for e in run.gov.log.entries}
    remaining = [
        step
        for step in plan.steps
        if step.invocation_id is not None and "result:" + step.invocation_id not in completed
    ]
    sources = {step.call_entry_index for step in remaining}
    if len(sources) > 1:
        raise ExecutionDenied("resume_sources_invalid")
    if remaining:
        source = run.gov.log.entries[remaining[0].call_entry_index or 0]
        run.executor.bind(source)
        run.deferred_ids = frozenset(
            m.call_id
            for m in source.call_manifest
            if "result:" + digest([run.gov.log.session.id, source.index, m.position])
            not in completed
        )
        run.executor.replays = {
            step.invocation_id: next(
                e for e in run.gov.log.entries if e.key == "intent:" + step.invocation_id
            )
            for step in remaining
            if step.kind == "rerun" and step.invocation_id is not None
        }
        visible = {t.name for t in run.template.tools}
        names = dict.fromkeys(
            m.tool
            for m in source.call_manifest
            if m.tool not in visible and m.tool in run.executor.registry.tools
        )
        run.recovery_tools = tuple(
            tool_spec(run.executor.registry.tools[name].tool) for name in names
        )
