"""Owner-bound Client facade."""

from collections.abc import AsyncIterator, Sequence

from tether.sessions import content
from tether.sessions.service import SessionService
from tether.types import AttachmentRef
from tether.types.client import Change, TurnHandle
from tether.types.execution import Actor, PendingConfirmation

from .reads import ClientReads


class Client(ClientReads):
    def __init__(self, service: SessionService, actor: Actor) -> None:
        super().__init__(service.access, actor)
        self._service = service

    async def pending(self, session_id: str) -> PendingConfirmation | None:
        return await self._access.metadata(
            self._actor,
            session_id,
            lambda: content.pending_view(
                self._access, self._service.engine, self._actor, session_id
            ),
        )

    def follow(
        self, *, session_id: str | None = None, since: int | None = None
    ) -> AsyncIterator[Change]:
        return self._service.followers.follow(self._actor, session_id=session_id, since=since)

    async def send(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str | None = None,
        attachments: Sequence[AttachmentRef] = (),
        spec: str | None = None,
    ) -> TurnHandle:
        return await self._service.turns.send(
            self._actor, session_id, text, request_id=request_id, attachments=attachments, spec=spec
        )

    async def cancel(self, session_id: str) -> bool:
        return await self._service.turns.cancel(self._actor, session_id)
