"""Current caller authorization and bounded, verified source disclosure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tether.engine.governance import reason_code
from tether.engine.observers.facade import Observers
from tether.types import Payload
from tether.types.canonical import canonical, digest
from tether.types.client import ClientLimits, ReadLimit
from tether.types.execution import (
    Actor,
    AuthzRequest,
    Decision,
    ExecutionDenied,
    HistoryRef,
    Policy,
    copy_actor,
)
from tether.types.history import HistoryProvider, HistoryValue
from tether.types.storage import SessionStore

T = TypeVar("T", bound=Payload | None)


class SessionAccess:
    def __init__(
        self,
        store: SessionStore,
        history: HistoryProvider,
        policy: Policy,
        limits: ClientLimits,
        *,
        observers: Observers | None = None,
    ):
        self.store, self.history, self.policy, self.limits = store, history, policy, limits
        self.observers = observers if observers is not None else Observers()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def bind(self) -> None:
        if self._loop is not None or self._closed:
            raise RuntimeError("tether_already_entered")
        self._loop = asyncio.get_running_loop()
        self.observers.delivery.bind()

    def check_loop(self) -> None:
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("wrong_event_loop")

    def check(self) -> None:
        self.check_loop()
        if self._closed or self._loop is None:
            raise RuntimeError("tether_not_active")
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("wrong_event_loop")

    def seal(self) -> None:
        self.check()
        self._closed = True

    async def allowed(
        self,
        actor: Actor,
        resource: str,
        action: str = "read_session",
        *,
        session_id: str | None = None,
    ) -> bool:
        self.check()
        actor = copy_actor(actor)
        with self.observers.client.gate(actor, resource, action, session_id) as gate:
            try:
                async with asyncio.timeout(self.limits.read_timeout_seconds):
                    value = await self.policy.authorize(
                        AuthzRequest(actor=copy_actor(actor), resource=resource, action=action)
                    )
                decision = Decision.model_validate(value)
            except Exception:
                gate.completed(False, "policy_unavailable")
                return False
            gate.completed(decision.allowed, decision.reason)
            return decision.allowed

    async def authorize(
        self,
        actor: Actor,
        resource: str,
        action: str = "read_session",
        *,
        session_id: str | None = None,
    ) -> None:
        if not await self.allowed(actor, resource, action, session_id=session_id):
            raise ExecutionDenied("policy_denied")

    def limit(self, count: object, *, maximum: int | None = None) -> None:
        self.check()
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 0 < count <= (maximum or self.limits.max_page_size)
        ):
            raise ReadLimit("read_limit")

    def position(self, start: object) -> None:
        self.check()
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ReadLimit("read_position")

    def store_for(self, actor: Actor) -> SessionStore:
        self.check()
        return self.store.for_actor(copy_actor(actor))

    def size(self, value: Payload) -> int:
        size = len(canonical(value.model_dump(mode="json")))
        if size > self.limits.max_read_bytes:
            raise ReadLimit("read_bytes")
        return size

    async def metadata(
        self,
        actor: Actor,
        resource: str,
        operation: Callable[[], Awaitable[T]],
        *,
        disclose: Callable[[T], Awaitable[T]] | None = None,
        session_id: str | None = None,
    ) -> T:
        self.check()
        async with asyncio.timeout(self.limits.read_timeout_seconds):
            await self.authorize(actor, resource, session_id=session_id)
            value = await operation()
            if value is not None:
                self.size(value)
            await self.authorize(actor, resource, session_id=session_id)
            if disclose is not None:
                value = await disclose(value)
                if value is not None:
                    self.size(value)
            return value

    async def watermark(self, actor: Actor) -> int:
        self.check()
        async with asyncio.timeout(self.limits.read_timeout_seconds):
            position = await self.store_for(actor).watermark(actor.id)
        self.position(position)
        return position

    async def hydrate(self, actor: Actor, session_id: str, ref: HistoryRef) -> HistoryValue:
        await self.authorize(
            actor,
            session_id,
            session_id=session_id,
        )
        if ref.kind == "model_exchange" and not await self.allowed(
            actor,
            session_id,
            "read_call_payload",
            session_id=session_id,
        ):
            return HistoryValue(ref=ref, status="denied", reason="policy_denied")
        if ref.owner != actor.id or ref.session_id != session_id:
            return HistoryValue(ref=ref, status="denied", reason="history_scope")
        try:
            async with asyncio.timeout(self.limits.read_timeout_seconds):
                values = await self.history.read((ref,), actor=copy_actor(actor))
            value = self.verify(ref, values)
        except Exception:
            value = HistoryValue(ref=ref, status="unavailable", reason="history_unavailable")
        await self.authorize(
            actor,
            session_id,
            session_id=session_id,
        )
        if ref.kind == "model_exchange" and not await self.allowed(
            actor,
            session_id,
            "read_call_payload",
            session_id=session_id,
        ):
            return HistoryValue(ref=ref, status="denied", reason="policy_denied")
        self.size(value)
        return value

    @staticmethod
    def verify(ref: HistoryRef, values: tuple[HistoryValue, ...]) -> HistoryValue:
        if len(values) != 1 or values[0].ref != ref:
            return HistoryValue(ref=ref, status="integrity_error", reason="history_integrity")
        value = values[0].model_copy(deep=True)
        if value.status == "ready":
            if (
                value.record is None
                or value.record.kind != ref.kind
                or digest(value.record.model_dump(mode="json")) != ref.sha256
            ):
                return HistoryValue(ref=ref, status="integrity_error", reason="history_integrity")
        else:
            value = value.model_copy(update={"record": None})
        return value.model_copy(
            update={
                "reason": reason_code(value.reason, "history_unavailable")
                if value.reason is not None
                else None
            }
        )
