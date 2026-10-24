"""Bounded owner-authorized metadata commands; the store serializes mutations."""

from tether.types.canonical import canonical
from tether.types.client import ReadLimit
from tether.types.execution import Actor, ExecutionDenied, copy_actor
from tether.types.storage import SessionInit, SessionPatch, SessionSummary, UnknownSession

from .access import SessionAccess


class MetadataCommands:
    def __init__(self, access: SessionAccess):
        self.access = access

    def _bound(self, value: SessionInit | SessionPatch) -> None:
        self.access.check()
        if value.settings is not None and "held" in value.settings:
            raise ExecutionDenied("managed_setting")
        if (
            len(canonical(value.model_dump(mode="json", exclude_unset=True)))
            > self.access.limits.max_request_bytes
        ):
            raise ReadLimit("request_bytes")

    async def create(self, actor: Actor, init: SessionInit) -> SessionSummary:
        actor, init = copy_actor(actor), init.model_copy(deep=True)
        self._bound(init)

        async def operation() -> SessionSummary:
            summary = await self.access.store.create(actor.id, init)
            await self.access.authorize(actor, summary.id)
            return summary

        return await self.access.metadata(actor, "owner:" + actor.id, operation)

    async def update(self, actor: Actor, session_id: str, patch: SessionPatch) -> SessionSummary:
        actor, patch = copy_actor(actor), patch.model_copy(deep=True)
        self._bound(patch)

        async def operation() -> SessionSummary:
            if await self.access.store.get(session_id, actor.id) is None:
                raise UnknownSession("unknown_session")
            await self.access.authorize(actor, session_id)
            return await self.access.store.update(session_id, actor.id, patch)

        return await self.access.metadata(actor, session_id, operation)
