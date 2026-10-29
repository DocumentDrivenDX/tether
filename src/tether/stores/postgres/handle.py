"""A lease-bound last-known journal prefix; uncertainty seals further writes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from tether.stores.projections import SessionIndex
from tether.types.execution import (
    Actor,
    CommitUnknown,
    Entry,
    ExecutionDenied,
    NewEntry,
    SessionState,
)
from tether.types.lifetime import WorkerLifetime
from tether.types.settlement import SettlementBinding, SettlementParticipant
from tether.types.storage import IntegrityError, LeaseLost

from .atomic import operation_key as batch_key
from .capture import Capture
from .leases import Appended, Prefix, Token
from .paired import PairedHistory, references

if TYPE_CHECKING:
    from .root import DatabaseRoot

T = TypeVar("T")


class TurnHandle:
    def __init__(self, store: Capture, token: Token, index: SessionIndex, prefix: Prefix) -> None:
        store.root.check_loop()
        self._store, self._token, self._index = store, token, index
        self._prefix = prefix
        self._lock = asyncio.Lock()
        self._history: PairedHistory | None = None
        self._poisoned = self._lost = self._closed = False

    @property
    def root(self) -> DatabaseRoot:
        return self._store.root

    def check_history(self, actor: Actor, session_id: str) -> None:
        self._ready()
        if (actor.id, actor.principal, session_id) != (
            self._store.actor.id,
            self._store.actor.principal,
            self._token.session_id,
        ):
            raise ExecutionDenied("history_transaction_scope")

    @property
    def history_root(self) -> object:
        return self.root

    def bind_history(self, root: object) -> PairedHistory:
        self._ready()
        if root is not self.root:
            raise ExecutionDenied("history_transaction_mismatch")
        if self._history is None:
            self._history = PairedHistory(self.root, self)
        return self._history

    @property
    def turn_id(self) -> str:
        return self._token.turn_id

    @property
    def workers(self) -> WorkerLifetime:
        return self._store.root.workers

    @property
    def session(self) -> SessionState:
        self._store.root.check_loop()
        return self._index.state.model_copy(deep=True)

    @property
    def entries(self) -> tuple[Entry, ...]:
        self._store.root.check_loop()
        return tuple(entry.model_copy(deep=True) for entry in self._index.entries)

    def _ready(self) -> None:
        self._store.root.check_loop()
        if self._closed or self._lost:
            raise LeaseLost("lease_lost")
        if self._poisoned:
            raise CommitUnknown("turn_requires_reopen")

    async def _guard(self, operation: Callable[[], Awaitable[T]]) -> T:
        self._ready()
        try:
            value = await operation()
        except (asyncio.CancelledError, CommitUnknown):
            self._poisoned = True
            raise
        except LeaseLost:
            self._lost = True
            raise
        except IntegrityError:
            self._poisoned = True
            raise
        self._ready()
        return value

    def _stage(self, result: Appended) -> tuple[SessionIndex, tuple[Entry, ...]]:
        staged = self._index.clone()
        for entry in sorted((*result.entries, *result.receipts), key=lambda value: value.index):
            if entry.index < len(staged.entries):
                if staged.entries[entry.index] != entry:
                    raise IntegrityError("turn_prefix_integrity")
            else:
                staged.apply(entry.model_copy(deep=True))
        staged.summary = staged.summary.model_copy(update={"last_seq": result.summary.last_seq})
        if staged.summary != result.summary:
            raise IntegrityError("turn_projection_integrity")
        return staged, tuple(entry.model_copy(deep=True) for entry in result.entries)

    async def _append(
        self,
        entries: tuple[NewEntry, ...],
        settlement: tuple[SettlementBinding, SettlementParticipant] | None = None,
        operation_key: str | None = None,
        expected_frontier: int | None = None,
    ) -> tuple[SessionIndex, Appended, tuple[Entry, ...]]:
        history = self._history.select(entries) if self._history is not None else ()
        if (
            operation_key is None
            and self._history is not None
            and any(references(entry) for entry in entries)
        ):
            operation_key = batch_key(entries)
        result = await self._store.commit(
            self._token,
            entries,
            prefix=self._prefix,
            history=history,
            settlement=settlement,
            operation_key=operation_key,
            expected_frontier=expected_frontier,
        )
        try:
            staged, accepted = await self._store.root.prepare(
                self._token.session_id, lambda: self._stage(result)
            )
            await self._store.heartbeat(self._token)
        except (asyncio.CancelledError, LeaseLost):
            raise
        except Exception as error:
            raise CommitUnknown("turn_projection_unknown") from error
        if self._history is not None:
            self._history.accepted(history)
        return staged, result, accepted

    async def append(
        self,
        entries: Sequence[NewEntry],
        *,
        operation_key: str | None = None,
        expected_frontier: int | None = None,
    ) -> tuple[Entry, ...]:
        self._ready()
        copied = tuple(entry.model_copy(deep=True) for entry in entries)
        async with self._lock:
            staged, result, accepted = await self._guard(
                lambda: self._append(
                    copied, operation_key=operation_key, expected_frontier=expected_frontier
                )
            )
            if result.prefix is None:
                self._poisoned = True
                raise CommitUnknown("turn_prefix_unknown")
            self._index, self._prefix = staged, result.prefix
            return accepted

    async def settle(
        self,
        entries: Sequence[NewEntry],
        binding: SettlementBinding,
        participant: SettlementParticipant,
    ) -> tuple[Entry, ...]:
        self._ready()
        copied = tuple(entry.model_copy(deep=True) for entry in entries)
        binding = SettlementBinding.model_validate(binding.model_dump(mode="json"))
        if binding.session_id != self._token.session_id or participant.id != binding.participant:
            raise ExecutionDenied("settlement_transaction_mismatch")
        async with self._lock:
            staged, result, accepted = await self._guard(
                lambda: self._append(copied, (binding, participant))
            )
            if result.prefix is None:
                self._poisoned = True
                raise CommitUnknown("turn_prefix_unknown")
            self._index, self._prefix = staged, result.prefix
            return accepted

    async def heartbeat(self) -> None:
        await self._guard(lambda: self._store.heartbeat(self._token))

    def seal(self) -> None:
        self._store.root.check_loop()
        self._closed = True

    async def aclose(self) -> None:
        self.seal()
        await self._store.release(self._token)
