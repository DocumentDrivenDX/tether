"""Bounded Tether-owned turn tasks, acceptance handles, and canonical request retries."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar
from uuid import uuid4

from tether.engine import Engine
from tether.types import AttachmentRef
from tether.types.canonical import canonical, input_digest
from tether.types.client import ReadLimit, RequestConflict, TurnHandle
from tether.types.execution import (
    Actor,
    AgentSpec,
    ExecutionDenied,
    TurnResult,
    TurnStart,
    copy_actor,
)
from tether.types.storage import SessionClosed, TurnInProgress, UnknownSession

from .access import SessionAccess
from .outcomes import outcome
from .supervision import supervise

T = TypeVar("T")


def observe(future: asyncio.Future[T]) -> None:
    if not future.cancelled():
        future.exception()


@dataclass
class Job:
    start: TurnStart
    digest: str
    ready: asyncio.Future[None]
    task: asyncio.Task[TurnResult] | None = None


class Handle:
    def __init__(
        self,
        owner: OwnedTurns,
        actor: Actor,
        session_id: str,
        turn_id: str,
        task: asyncio.Task[TurnResult] | None,
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
        async with asyncio.timeout(access.limits.read_timeout_seconds):
            value = await outcome(
                access, self._owner.engine, self._actor, self._session_id, self.turn_id
            )
            access.size(value)
            await access.authorize(self._actor, self._session_id)
        return value

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
        view = await self.access.store.get(session_id, actor.id)
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
        self.access.check()
        if self.closed or len(self.jobs) >= self.access.limits.max_active_turns:
            raise ReadLimit("active_turn_limit")
        job = Job(start, identity, asyncio.get_running_loop().create_future())
        job.ready.add_done_callback(observe)
        self.jobs[session_id] = job
        job.task = asyncio.create_task(self._run(actor, session_id, text, definition, job))
        job.task.add_done_callback(lambda task: self._finished(session_id, job, task))
        return await self._accepted(actor, session_id, job)

    async def _existing(
        self, actor: Actor, session_id: str, request_id: str, identity: str
    ) -> TurnHandle | None:
        entries = await self.access.store.lookup(
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
            async with self.access.store.turn(
                session_id, actor.id, turn_id=job.start.turn_id
            ) as log:
                if job.start.request_id is not None:
                    existing = await self._existing(
                        actor, session_id, job.start.request_id, job.digest
                    )
                    if existing is not None:
                        job.start = job.start.model_copy(update={"turn_id": existing.turn_id})
                        job.ready.set_result(None)
                        return await existing.result()
                return await supervise(
                    self.engine.run_turn(
                        actor,
                        log,
                        text,
                        spec=spec,
                        start=job.start,
                        accepted=lambda: job.ready.set_result(None),
                    ),
                    log,
                    self.access.limits.heartbeat_interval_seconds,
                )
        except BaseException as error:
            if not job.ready.done():
                job.ready.set_exception(error)
            raise

    def _finished(self, session_id: str, job: Job, task: asyncio.Task[TurnResult]) -> None:
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
        if await self.access.store.get(session_id, actor.id) is None:
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
