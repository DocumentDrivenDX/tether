"""Fabricated, volatile execution doubles. No lease, restart or durability claim."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from tether.types import Message
from tether.types.canonical import digest
from tether.types.execution import Actor, Entry, EntryConflict, HistoryRef, NewEntry, SessionState
from tether.types.lifetime import WorkerLifetime


class MemoryTurnLog:
    durability = "none"

    def __init__(self, session: SessionState, *, turn_id: str | None = None) -> None:
        self.workers = WorkerLifetime()
        self.turn_id = turn_id
        self.session = session
        self._entries: list[Entry] = []

    @property
    def entries(self) -> tuple[Entry, ...]:
        return tuple(e.model_copy(deep=True) for e in self._entries)

    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]:
        staged = list(self._entries)
        result: list[Entry] = []
        for value in entries:
            previous = next(
                (e for e in staged if value.key is not None and e.key == value.key), None
            )
            if previous is not None:
                if value != NewEntry.model_validate(previous.model_dump(exclude={"index", "at"})):
                    raise EntryConflict("entry_conflict")
                result.append(previous)
            else:
                entry = Entry(**value.model_dump(), index=len(staged), at=datetime.now(UTC))
                staged.append(entry)
                result.append(entry)
        self._entries = staged
        return tuple(e.model_copy(deep=True) for e in result)


class MemoryHistory:
    durability = "none"

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], tuple[str, Message]] = {}

    async def put(self, *, actor: Actor, session_id: str, key: str, message: Message) -> HistoryRef:
        address = (session_id, key)
        value = (actor.id, message.model_copy(deep=True))
        if address in self._values and self._values[address] != value:
            raise EntryConflict("history_conflict")
        self._values[address] = value
        return HistoryRef(
            provider="memory",
            key=key,
            sha256=digest(message.model_dump(mode="json")),
            owner=actor.id,
            session_id=session_id,
            kind="message",
        )

    async def read(self, ref: HistoryRef, *, actor: Actor, session_id: str) -> Message:
        value = self._values.get((session_id, ref.key))
        if (
            (ref.provider, ref.owner, ref.session_id, ref.kind, ref.component, ref.codec)
            != ("memory", actor.id, session_id, "message", "record", "tether-message-v1")
            or value is None
            or value[0] != actor.id
        ):
            raise PermissionError("history_denied")
        message = value[1]
        if digest(message.model_dump(mode="json")) != ref.sha256:
            raise ValueError("history_integrity")
        return message.model_copy(deep=True)
