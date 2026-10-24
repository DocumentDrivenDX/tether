"""Owner-bound Client facade."""

from tether.sessions import content
from tether.sessions.service import SessionService
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
