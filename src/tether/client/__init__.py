"""Owner-bound Client facade."""

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Literal

from tether.sessions import closing, content, links, resolution
from tether.sessions.metadata import MetadataCommands
from tether.sessions.service import SessionService
from tether.types import AttachmentRef, JsonValue
from tether.types.client import UNSET, Change, TurnHandle, Unset
from tether.types.execution import Actor, Answer, Entry, HistoryRef, PendingConfirmation, Resolution
from tether.types.storage import SessionInit, SessionPatch, SessionSummary

from .operations import OperationClient
from .reads import ClientReads


class Client(OperationClient, ClientReads):
    def __init__(self, service: SessionService, actor: Actor) -> None:
        super().__init__(service.access, actor, service.archives)
        self._service = service
        self._metadata = MetadataCommands(service.access)

    async def pending(self, session_id: str) -> PendingConfirmation | None:
        return await self._access.metadata(
            self._actor,
            session_id,
            lambda: content.pending_view(
                self._access, self._service.engine, self._actor, session_id
            ),
            session_id=session_id,
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

    async def confirm(
        self,
        session_id: str,
        invocation_id: str,
        answer: Answer,
    ) -> TurnHandle | None:
        return await self._service.turns.confirm(self._actor, session_id, invocation_id, answer)

    async def create(
        self,
        *,
        ref: str | None = None,
        spec: str | None = None,
        title: str | None = None,
        tags: Sequence[str] = (),
        settings: Mapping[str, JsonValue] | None = None,
    ) -> SessionSummary:
        return await self._metadata.create(
            self._actor,
            SessionInit(
                ref=ref, spec=spec, title=title, tags=tuple(tags), settings=dict(settings or {})
            ),
        )

    async def update(
        self,
        session_id: str,
        *,
        title: str | Unset | None = UNSET,
        tags: Sequence[str] | None = None,
        settings: Mapping[str, JsonValue] | None = None,
    ) -> SessionSummary:
        changes: dict[str, JsonValue] = {}
        if not isinstance(title, Unset):
            changes["title"] = title
        if tags is not None:
            changes["tags"] = list(tags)
        if settings is not None:
            changes["settings"] = dict(settings)
        return await self._metadata.update(
            self._actor, session_id, SessionPatch.model_validate(changes)
        )

    async def close(self, session_id: str) -> SessionSummary:
        return await closing.close_session(
            self._access, self._service.engine, self._actor, session_id
        )

    async def resolve(
        self,
        session_id: str,
        invocation_id: str,
        outcome: Literal["completed", "not_completed"],
        *,
        reason: str,
    ) -> SessionSummary:
        return await resolution.resolve(
            self._access,
            self._service.engine,
            self._actor,
            session_id,
            Resolution(invocation_id=invocation_id, outcome=outcome, reason=reason),
        )

    async def link_history(
        self,
        session_id: str,
        message_index: int,
        previous: HistoryRef,
        verified: HistoryRef,
        *,
        key: str,
    ) -> Entry:
        return await links.bind(
            self._access, self._actor, session_id, message_index, previous, verified, key=key
        )

    async def resume(self, session_id: str) -> TurnHandle:
        return await self._service.turns.resume(self._actor, session_id)

    async def restore(self, session_id: str) -> SessionSummary:
        return await self._service.archives.restore(self._actor, session_id)
