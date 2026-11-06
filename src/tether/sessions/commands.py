"""Client tool commands share bounded task ownership, leases and request retries."""

from uuid import uuid4

from tether.types.canonical import canonical
from tether.types.client import ReadLimit, TurnHandle
from tether.types.commands import AuxiliaryCall, InvokeTool, ReconcileSettlement, SpecTransition
from tether.types.execution import (
    Actor,
    AgentSpec,
    ExecutionDenied,
    TurnResult,
    TurnStart,
    copy_actor,
)
from tether.types.storage import SessionClosed, UnknownSession

from .outcomes import read_outcome
from .supervision import supervise
from .turns import Job, OwnedTurns


class CommandTurns:
    def __init__(self, turns: OwnedTurns) -> None:
        self.turns = turns

    async def invoke(
        self,
        actor: Actor,
        session_id: str,
        command: InvokeTool | AuxiliaryCall | ReconcileSettlement | SpecTransition,
    ) -> TurnHandle:
        turns = self.turns
        turns.access.check()
        actor, command = (
            copy_actor(actor),
            type(command).model_validate(command.model_dump(mode="json")),
        )
        if len(canonical(command.model_dump(mode="json"))) > turns.access.limits.max_request_bytes:
            raise ReadLimit("request_bytes")
        await turns.access.authorize(actor, session_id, session_id=session_id)
        view = await turns.access.store_for(actor).get(session_id, actor.id)
        if view is None:
            raise UnknownSession("unknown_session")
        existing = await turns.existing(actor, session_id, command.request_id, command.fingerprint)
        if existing is not None:
            return existing
        job = turns.jobs.get(session_id)
        if job is not None and job.start.request_id == command.request_id:
            from tether.types.client import RequestConflict

            if job.digest != command.fingerprint:
                raise RequestConflict("request_conflict")
            return await turns.accepted(actor, session_id, job)
        if view.summary.status == "closed":
            raise SessionClosed("session_closed")
        selected = command.spec or view.summary.spec_id or turns.default_spec
        if selected is None:
            raise ExecutionDenied("spec_unavailable")
        spec = turns.engine.spec(selected)
        job = turns.launch(
            session_id,
            TurnStart(turn_id=str(uuid4()), request_id=command.request_id),
            command.fingerprint,
            lambda job: self._run(actor, session_id, command, spec, job),
        )
        return await turns.accepted(actor, session_id, job)

    async def _run(
        self,
        actor: Actor,
        session_id: str,
        command: InvokeTool | AuxiliaryCall | ReconcileSettlement | SpecTransition,
        spec: AgentSpec,
        job: Job,
    ) -> TurnResult:
        turns = self.turns
        try:
            async with turns.access.store_for(actor).turn(
                session_id, actor.id, turn_id=job.start.turn_id
            ) as log:
                existing = await turns.existing(
                    actor, session_id, command.request_id, command.fingerprint
                )
                if existing is not None:
                    job.start = job.start.model_copy(update={"turn_id": existing.turn_id})
                    job.accept()
                    return await read_outcome(
                        turns.access, turns.engine, actor, session_id, existing.turn_id
                    )
                return await supervise(
                    turns.engine.commands.invoke(
                        actor, log, command, spec=spec, accepted=job.accept
                    ),
                    log,
                    turns.access.limits.heartbeat_interval_seconds,
                )
        except BaseException as error:
            if not job.ready.done():
                job.ready.set_exception(error)
            raise
