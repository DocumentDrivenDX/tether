"""Adapter-owned named SQL operations; a generic tool never receives a connection."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable

from tether.types import JsonValue
from tether.types.canonical import canonical
from tether.types.execution import Actor, ExecutionDenied, copy_actor
from tether.types.settlement import SettlementBinding, SettlementOutcome, SettlementParticipant
from tether.types.storage import StoreError

from .journal import Journal
from .root import DatabaseRoot
from .rows import Connection, Frame


class SettlementTransaction:
    def __init__(
        self, connection: Connection, operations: Mapping[str, str], maximum: int, max_bytes: int
    ) -> None:
        self._connection, self._operations = connection, operations
        self._remaining, self._max_bytes = maximum, max_bytes
        self._active = True

    async def execute(self, operation: str, parameters: Sequence[JsonValue]) -> int:
        if not self._active or operation not in self._operations or self._remaining <= 0:
            raise StoreError("settlement_operation_refused")
        copied = tuple(parameters)
        if len(canonical(list(copied))) > self._max_bytes:
            raise StoreError("settlement_operation_bytes")
        self._remaining -= 1
        cursor = await self._connection.execute(self._operations[operation].encode(), copied)
        return cursor.rowcount

    def close(self) -> None:
        self._active = False


@runtime_checkable
class SQLParticipant(Protocol):
    root: DatabaseRoot
    operations: Mapping[str, str]

    async def apply(
        self, transaction: SettlementTransaction, binding: SettlementBinding, actor: Actor
    ) -> None: ...


class PostgresSettlementParticipant:
    profile: Literal["independent", "shared"] = "shared"

    def __init__(
        self,
        root: DatabaseRoot,
        *,
        id: str,
        operations: Mapping[str, str],
        apply: Callable[[SettlementTransaction, SettlementBinding, Actor], Awaitable[None]],
    ) -> None:
        if not id or not operations or len(operations) > root.limits.page_limit:
            raise ValueError("settlement_participant_invalid")
        if len(canonical(dict(operations))) > root.limits.max_batch_bytes:
            raise ValueError("settlement_participant_bytes")
        self.id, self.root = id, root
        self.operations = MappingProxyType(dict(operations))
        self.apply = apply

    async def settle(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome:
        raise ExecutionDenied("settlement_shared_requires_transaction")

    async def reconcile(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome:
        raise ExecutionDenied("settlement_shared_requires_transaction")


async def apply_participant(
    connection: Connection,
    root: DatabaseRoot,
    actor: Actor,
    binding: SettlementBinding,
    participant: SQLParticipant,
) -> None:
    if participant.root is not root or (binding.owner, binding.actor_id, binding.principal) != (
        actor.id,
        actor.id,
        actor.principal or actor.id,
    ):
        raise ExecutionDenied("settlement_transaction_mismatch")
    transaction = SettlementTransaction(
        connection, participant.operations, root.limits.page_limit, root.limits.max_batch_bytes
    )
    try:
        await participant.apply(transaction, binding, copy_actor(actor))
    finally:
        transaction.close()


async def enlist(
    store: "Journal",
    connection: Connection,
    frame: "Frame",
    binding: SettlementBinding,
    participant: "SettlementParticipant",
) -> None:
    key = "settlement:" + binding.settlement_id + ":pending"
    previous = await store.existing(
        connection, frame.projection.state.id, key, store.root.limits.max_batch_bytes
    )
    if previous is None:
        if not isinstance(participant, SQLParticipant):
            raise ExecutionDenied("settlement_shared_unsupported")
        await apply_participant(connection, store.root, store.actor, binding, participant)
