"""Bounded Tether-owned turn tasks, acceptance handles, and canonical request retries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar
from uuid import uuid4

from tether.engine import Engine
from tether.engine.reconstruction import requested_turn
from tether.types import AttachmentRef
from tether.types.canonical import canonical, input_digest
from tether.types.client import ReadLimit, RequestConflict, TurnHandle
from tether.types.execution import (
    Actor,
    AgentSpec,
    Answer,
    ExecutionDenied,
    NotInterrupted,
    TurnResult,
    TurnStart,
    copy_actor,
)
from tether.types.storage import SessionClosed, TurnInProgress, UnknownSession, Unsupported

from .access import SessionAccess
from .outcomes import read_outcome
from .supervision import supervise

T = TypeVar("T")


def observe(future: asyncio.Future[T]) -> None:
    if not future.cancelled():
        future.exception()


@dataclass
class Job:
    start: TurnStart
    digest: str
    ready: asyncio.Future[bool]
    accepted: bool = False
    task: asyncio.Task[TurnResult | None] | None = None

    def accept(self) -> None:
        self.accepted = True
        self.ready.set_result(True)


class Handle:
    def __init__(
        self,
        owner: OwnedTurns,
        actor: Actor,
        session_id: str,
        turn_id: str,
        task: asyncio.Task[TurnResult | None] | None,
    ):
        self._owner, self._actor = owner, copy_actor(actor)
        self._session_id, self._turn_id, self._task = session_id, turn_id, task

    @property
    def turn_id(self) -> str:
        return self._turn_id

    async def result(self) -> TurnResult:
        access = self._owner.access
        await access.authorize(self._actor, self._session_id)
        if self._task is not None:
            await asyncio.shield(self._task)
        current = self._owner.jobs.get(self._session_id)
        if (
            current is not None
            and current.accepted
            and current.start.turn_id == self.turn_id
            and current.task is not None
        ):
            await asyncio.shield(current.task)
        return await read_outcome(
            access, self._owner.engine, self._actor, self._session_id, self.turn_id
        )

    async def cancel(self) -> None:
        await self._owner.cancel(self._actor, self._session_id, turn_id=self.turn_id)


class OwnedTurns:
    def __init__(self, access: SessionAccess, engine: Engine, default_spec: str | None):
        self.access, self.engine, self.default_spec = access, engine, default_spec
        self.jobs: dict[str, Job] = {}
        self.closed = False

    def _input(
        self,
        text: object,
        attachments: Sequence[AttachmentRef],
        request_id: str | None,
        spec: str | None,
    ) -> tuple[TurnStart, str]:
        maximum = self.access.limits.max_request_bytes
        if not isinstance(text, str) or len(text) > maximum or len(attachments) > maximum:
            raise ReadLimit("request_bytes")
        start = TurnStart(
            turn_id=str(uuid4()),
            request_id=request_id,
            requested_spec=spec,
            attachments=tuple(attachments),
        ).model_copy(deep=True)
        if len(canonical({"text": text, "start": start.model_dump(mode="json")})) > maximum:
            raise ReadLimit("request_bytes")
        return start, input_digest(text, start.attachments, spec)

    async def send(
        self,
        actor: Actor,
        session_id: str,
        text: str,
        *,
        request_id: str | None = None,
        attachments: Sequence[AttachmentRef] = (),
        spec: str | None = None,
    ) -> TurnHandle:
        self.access.check()
        actor = copy_actor(actor)
        start, identity = self._input(text, attachments, request_id, spec)
        await self.access.authorize(actor, session_id)
        view = await self.access.store_for(actor).get(session_id, actor.id)
        if view is None:
            raise UnknownSession("unknown_session")
        if request_id is not None:
            existing = await self._existing(actor, session_id, request_id, identity)
            if existing is not None:
                return existing
        job = self.jobs.get(session_id)
        if job is not None:
            if request_id is None or job.start.request_id != request_id:
                raise TurnInProgress("turn_in_progress")
            if job.digest != identity:
                raise RequestConflict("request_conflict")
            return await self._accepted(actor, session_id, job)
        if view.summary.status == "closed":
            raise SessionClosed("session_closed")
        selected = spec if spec is not None else view.summary.spec_id or self.default_spec
        if selected is None:
            raise ExecutionDenied("spec_unavailable")
        definition = self.engine.spec(selected)
        job = self._launch(
            session_id,
            start,
            identity,
            lambda job: self._run(actor, session_id, text, definition, job),
        )
        return await self._accepted(actor, session_id, job)

    def _launch(
        self,
        session_id: str,
        start: TurnStart,
        identity: str,
        operation: Callable[[Job], Awaitable[TurnResult | None]],
    ) -> Job:
        self.access.check()
        if session_id in self.jobs:
            raise TurnInProgress("turn_in_progress")
        if self.closed or len(self.jobs) >= self.access.limits.max_active_turns:
            raise ReadLimit("active_turn_limit")
        job = Job(start, identity, asyncio.get_running_loop().create_future())
        job.ready.add_done_callback(observe)
        self.jobs[session_id] = job

        async def run() -> TurnResult | None:
            return await operation(job)

        job.task = asyncio.create_task(run())
        job.task.add_done_callback(lambda task: self._finished(session_id, job, task))
        return job

    async def confirm(
        self,
        actor: Actor,
        session_id: str,
        invocation_id: str,
        answer: Answer,
    ) -> TurnHandle | None:
        self.access.check()
        actor, answer = copy_actor(actor), answer.model_copy(deep=True)
        if (
            len(invocation_id) > self.access.limits.max_request_bytes
            or len(canonical(answer.model_dump(mode="json"))) > self.access.limits.max_request_bytes
        ):
            raise ReadLimit("request_bytes")
        turn_id = await self._pending_turn(actor, session_id, invocation_id)
        if turn_id is None or session_id in self.jobs:
            return None
        job = self._launch(
            session_id,
            TurnStart(turn_id=turn_id),
            "approval:" + invocation_id,
            lambda job: self._answer(actor, session_id, invocation_id, answer, job),
        )
        accepted = await asyncio.shield(job.ready)
        await self.access.authorize(actor, session_id)
        if not accepted:
            return None
        return Handle(self, actor, session_id, job.start.turn_id, job.task)

    async def resume(self, actor: Actor, session_id: str) -> TurnHandle:
        self.access.check()
        actor = copy_actor(actor)
        capabilities = self.access.store_for(actor).capabilities
        if capabilities.durability == "none" or not capabilities.fencing:
            raise Unsupported("unsupported_store")
        view = await self.access.metadata(
            actor, session_id, lambda: self.access.store_for(actor).get(session_id, actor.id)
        )
        if view is None:
            raise UnknownSession("unknown_session")
        if view.summary.status == "closed":
            raise SessionClosed("session_closed")
        if session_id in self.jobs or view.summary.status == "running":
            raise TurnInProgress("turn_in_progress")
        if not view.summary.resumable or view.summary.status != "interrupted":
            raise NotInterrupted("not_interrupted")
        job = self._launch(
            session_id,
            TurnStart(turn_id=str(uuid4())),
            "resume",
            lambda job: self._resume(actor, session_id, job),
        )
        return await self._accepted(actor, session_id, job)

    async def _resume(self, actor: Actor, session_id: str, job: Job) -> TurnResult:
        try:
            async with self.access.store_for(actor).turn(
                session_id, actor.id, turn_id=job.start.turn_id
            ) as log:
                return await supervise(
                    self.engine.resume(actor, log, accepted=job.accept),
                    log,
                    self.access.limits.heartbeat_interval_seconds,
                )
        except BaseException as error:
            if not job.ready.done():
                job.ready.set_exception(error)
            raise

    async def _pending_turn(self, actor: Actor, session_id: str, invocation_id: str) -> str | None:
        async with asyncio.timeout(self.access.limits.read_timeout_seconds):
            view = await self.access.metadata(
                actor, session_id, lambda: self.access.store_for(actor).get(session_id, actor.id)
            )
            if view is None:
                raise UnknownSession("unknown_session")
            if view.pending is None or view.pending.invocation_id != invocation_id:
                return None
            entries = await self.access.store_for(actor).lookup(
                session_id,
                actor.id,
                kind="invocation",
                value=invocation_id,
                limit=self.access.limits.max_lookup_entries,
                max_bytes=self.access.limits.max_read_bytes,
            )
            await self.access.authorize(actor, session_id)
            return requested_turn(entries, invocation_id)

    async def _answer(
        self,
        actor: Actor,
        session_id: str,
        invocation_id: str,
        answer: Answer,
        job: Job,
    ) -> TurnResult | None:
        try:
            async with self.access.store_for(actor).turn(
                session_id, actor.id, turn_id=job.start.turn_id
            ) as log:
                result = await supervise(
                    self.engine.answer(
                        actor,
                        log,
                        invocation_id,
                        answer,
                        accepted=job.accept,
                    ),
                    log,
                    self.access.limits.heartbeat_interval_seconds,
                )
                if not job.ready.done():
                    job.ready.set_result(False)
                return result
        except (TurnInProgress, SessionClosed):
            if job.ready.done():
                raise
            job.ready.set_result(False)
            return None
        except BaseException as error:
            if not job.ready.done():
                job.ready.set_exception(error)
            raise

    async def _existing(
        self, actor: Actor, session_id: str, request_id: str, identity: str
    ) -> TurnHandle | None:
        entries = await self.access.store_for(actor).lookup(
            session_id,
            actor.id,
            kind="key",
            value="request:" + request_id,
            limit=self.access.limits.max_lookup_entries,
            max_bytes=self.access.limits.max_read_bytes,
        )
        if not entries:
            return None
        source = entries[0]
        if (
            source.kind != "turn_started"
            or source.turn_id is None
            or source.facts.get("input_digest") != identity
        ):
            raise RequestConflict("request_conflict")
        job = self.jobs.get(session_id)
        task = job.task if job is not None and job.start.turn_id == source.turn_id else None
        await self.access.authorize(actor, session_id)
        return Handle(self, actor, session_id, source.turn_id, task)

    async def _accepted(self, actor: Actor, session_id: str, job: Job) -> TurnHandle:
        await asyncio.shield(job.ready)
        await self.access.authorize(actor, session_id)
        return Handle(self, actor, session_id, job.start.turn_id, job.task)

    async def _run(
        self, actor: Actor, session_id: str, text: str, spec: AgentSpec, job: Job
    ) -> TurnResult:
        try:
            async with self.access.store_for(actor).turn(
                session_id, actor.id, turn_id=job.start.turn_id
            ) as log:
                if job.start.request_id is not None:
                    existing = await self._existing(
                        actor, session_id, job.start.request_id, job.digest
                    )
                    if existing is not None:
                        job.start = job.start.model_copy(update={"turn_id": existing.turn_id})
                        job.accept()
                        return await read_outcome(
                            self.access, self.engine, actor, session_id, existing.turn_id
                        )
                return await supervise(
                    self.engine.run_turn(
                        actor,
                        log,
                        text,
                        spec=spec,
                        start=job.start,
                        accepted=job.accept,
                    ),
                    log,
                    self.access.limits.heartbeat_interval_seconds,
                )
        except BaseException as error:
            if not job.ready.done():
                job.ready.set_exception(error)
            raise

    def _finished(self, session_id: str, job: Job, task: asyncio.Task[TurnResult | None]) -> None:
        if not job.ready.done():
            if task.cancelled():
                job.ready.cancel()
            else:
                job.ready.set_exception(task.exception() or RuntimeError("turn_not_accepted"))
        if self.jobs.get(session_id) is job:
            self.jobs.pop(session_id)
        observe(task)

    async def cancel(self, actor: Actor, session_id: str, *, turn_id: str | None = None) -> bool:
        await self.access.authorize(actor, session_id)
        if await self.access.store_for(actor).get(session_id, actor.id) is None:
            raise UnknownSession("unknown_session")
        await self.access.authorize(actor, session_id)
        job = self.jobs.get(session_id)
        if (
            job is None
            or job.task is None
            or (turn_id is not None and job.start.turn_id != turn_id)
        ):
            return False
        if job.task.done():
            return False
        job.task.cancel()
        return True

    def seal(self) -> None:
        self.closed = True
        for job in tuple(self.jobs.values()):
            if job.task is not None:
                job.task.cancel()

    async def wait_closed(self) -> None:
        tasks = [job.task for job in self.jobs.values() if job.task is not None]
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
