"""Caller-origin operations use the existing tool gates and journal authority."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from types import MappingProxyType

from tether.ports import ModelPort
from tether.types import Message, ToolCall
from tether.types.auxiliary import AuxiliaryProfile
from tether.types.canonical import digest
from tether.types.commands import AuxiliaryCall, InvokeTool, ReconcileSettlement, SpecTransition
from tether.types.execution import (
    Actor,
    AgentSpec,
    ExecutionDenied,
    NewEntry,
    ResultGuard,
    TurnLog,
    TurnResult,
)
from tether.types.history import HistoryProvider

from .auxiliary import AuxiliaryOperation, run_auxiliary
from .context import ContextBoundary
from .effects import unresolved_effects
from .governance import Governance
from .settlement import reconcile
from .tools import Registry, ToolExecutor
from .transitions import transition
from .workers import Workers


async def invoke(
    gov: Governance,
    command: InvokeTool,
    spec: AgentSpec,
    registry: Registry,
    workers: Workers,
    guards: tuple[ResultGuard, ...],
    accepted: Callable[[], None] | None,
) -> TurnResult:
    await gov.authorize("read_session", gov.log.session.id)
    if unresolved_effects(gov.log.entries) or any(
        "result:" + digest([gov.log.session.id, entry.index, manifest.position])
        not in {e.key for e in gov.log.entries}
        for entry in gov.log.entries
        for manifest in entry.call_manifest
    ):
        raise ExecutionDenied("effect_unresolved")
    if command.tool_name not in registry.tools:
        raise ExecutionDenied("tool_unavailable")
    call = ToolCall(
        id=digest([gov.log.session.id, command.fingerprint]),
        name=command.tool_name,
        arguments=command.arguments,
    )
    ref = await gov.history.put(
        actor=gov.actor,
        session_id=gov.log.session.id,
        key="command:" + command.request_id,
        message=Message(role="user", content="", tool_calls=(call,)),
    )
    spec_digest = digest(spec.model_dump(mode="json"))
    source = (
        await gov.commit(
            [
                NewEntry(
                    kind="turn_started",
                    turn_id=gov.turn_id,
                    key="request:" + command.request_id,
                    facts={
                        "origin": "client",
                        "input_digest": command.fingerprint,
                        "spec_id": spec.id,
                        "spec_version": spec.version,
                        "spec_digest": spec_digest,
                    },
                ),
                NewEntry(
                    kind="tool_command",
                    turn_id=gov.turn_id,
                    history_ref=ref,
                    call_manifest=(registry.manifest(call, 0),),
                    facts={
                        "origin": "client",
                        "request_id": command.request_id,
                        "spec_digest": spec_digest,
                    },
                ),
            ]
        )
    )[1]
    if accepted is not None:
        accepted()
    executor = ToolExecutor(gov, registry, spec, workers, guards)
    executor.bind(source)
    try:
        message = await executor.execute(call)
    except asyncio.CancelledError:
        await asyncio.shield(gov.fact("turn_ended", stop_reason="cancelled"))
        raise
    except ExecutionDenied as error:
        await gov.fact("turn_ended", stop_reason="tool_denied", reason=error.reason)
        return TurnResult(turn_id=gov.turn_id, stop_reason="tool_denied", reason=error.reason)
    await gov.fact("turn_ended", stop_reason="final")
    return TurnResult(
        turn_id=gov.turn_id, stop_reason="final", messages=(message,) if message is not None else ()
    )


class CommandEngine:
    def __init__(
        self,
        operation: Callable[[Actor, TurnLog], AbstractContextManager[None]],
        governance: Callable[[Actor, TurnLog, str], Governance],
        registry: Callable[[], Registry],
        workers: Workers,
        guards: tuple[ResultGuard, ...],
        *,
        context: ContextBoundary,
        model: ModelPort,
        history: Callable[[Governance], HistoryProvider | None],
        profiles: tuple[AuxiliaryProfile, ...],
    ) -> None:
        self.operation, self.governance = operation, governance
        self.registry, self.workers, self.guards = registry, workers, guards
        self.context, self.model, self.history = context, model, history
        if len({profile.id for profile in profiles}) != len(profiles):
            raise ValueError("auxiliary_profile_conflict")
        self.profiles = MappingProxyType(
            {profile.id: profile.model_copy(deep=True) for profile in profiles}
        )

    async def invoke(
        self,
        actor: Actor,
        log: TurnLog,
        command: InvokeTool | AuxiliaryCall | ReconcileSettlement | SpecTransition,
        *,
        spec: AgentSpec,
        accepted: Callable[[], None] | None = None,
    ) -> TurnResult:
        from uuid import uuid4

        with self.operation(actor, log):
            gov = self.governance(actor, log, log.turn_id or str(uuid4()))
            gov.spec_id, gov.spec_version = spec.id, spec.version
            if isinstance(command, SpecTransition):
                return await transition(gov, command, spec, self.registry(), accepted)
            if isinstance(command, ReconcileSettlement):
                await gov.fact(
                    "turn_started",
                    key="request:" + command.request_id,
                    origin=command.origin,
                    input_digest=command.fingerprint,
                    spec_id=spec.id,
                    spec_version=spec.version,
                    spec_digest=digest(spec.model_dump(mode="json")),
                )
                if accepted is not None:
                    accepted()
                await reconcile(gov, self.workers, command.settlement_id)
                await gov.fact("turn_ended", stop_reason="final")
                return TurnResult(turn_id=gov.turn_id, stop_reason="final")
            if isinstance(command, AuxiliaryCall):
                profile = self.profiles.get(command.profile)
                if profile is None:
                    raise ExecutionDenied("auxiliary_profile_unavailable")
                if command.through_index != len(log.entries) - 1 or unresolved_effects(log.entries):
                    raise ExecutionDenied("auxiliary_source_changed")
                history = self.history(gov)
                operation = AuxiliaryOperation(
                    gov, command, spec, profile, self.context, self.model, self.workers, history
                )
                return await run_auxiliary(operation, accepted)
            return await invoke(
                gov, command, spec, self.registry(), self.workers, self.guards, accepted
            )


async def resume_invocation(
    gov: Governance,
    registry: Registry,
    workers: Workers,
    guards: tuple[ResultGuard, ...],
    spec: AgentSpec,
    accepted: Callable[[], None] | None,
) -> TurnResult:
    from .effects import unresolved_settlements
    from .recovery import interrupted_turn, resume_chain

    parent = interrupted_turn(gov.log.entries)
    chain = resume_chain(gov.log.entries, parent)
    if len(chain) > gov.limits.max_resumes:
        raise ExecutionDenied("resume_limit")
    source = next(
        (
            entry
            for entry in gov.log.entries
            if entry.kind == "tool_command" and entry.turn_id in chain
        ),
        None,
    )
    if source is None or len(source.call_manifest) != 1:
        raise ExecutionDenied("client_operation_source_invalid")
    manifest = source.call_manifest[0]
    invocation = digest([gov.log.session.id, source.index, manifest.position])
    if invocation in unresolved_settlements(gov.log.entries):
        raise ExecutionDenied("settlement_reconciliation_required")
    await gov.authorize("read_session", gov.log.session.id)
    await gov.authorize("resume", gov.log.session.id)
    await gov.fact(
        "turn_started",
        origin="client",
        resumes=parent,
        spec_id=spec.id,
        spec_version=spec.version,
        spec_digest=digest(spec.model_dump(mode="json")),
    )
    if accepted is not None:
        accepted()
    intent = next((entry for entry in gov.log.entries if entry.key == "intent:" + invocation), None)
    if intent is not None and manifest.replay != "safe":
        await gov.fact("turn_ended", stop_reason="recovery_blocked", reason="unsafe_replay")
        return TurnResult(
            turn_id=gov.turn_id, stop_reason="recovery_blocked", reason="unsafe_replay"
        )
    proposal = await gov.read(source.history_ref, source_index=source.index)
    executor = ToolExecutor(gov, registry, spec, workers, guards)
    executor.bind(source)
    if intent is not None:
        executor.replays[invocation] = intent
    try:
        message = await executor.execute(proposal.tool_calls[manifest.position])
    except asyncio.CancelledError:
        await asyncio.shield(gov.fact("turn_ended", stop_reason="cancelled"))
        raise
    await gov.fact("turn_ended", stop_reason="final")
    return TurnResult(
        turn_id=gov.turn_id, stop_reason="final", messages=(message,) if message else ()
    )
