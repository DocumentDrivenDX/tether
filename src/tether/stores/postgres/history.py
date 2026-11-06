"""Immutable canonical-byte history, independently committed before journal references."""

from collections.abc import Sequence
from typing import Literal

from psycopg import AsyncConnection, sql

from tether.types.canonical import canonical, digest
from tether.types.execution import (
    Actor,
    EntryConflict,
    ExecutionDenied,
    HistoryRef,
    TurnLog,
    copy_actor,
)
from tether.types.history import (
    ExchangeBinding,
    ExchangeRefs,
    HistoryCapabilities,
    HistoryProvider,
    HistoryRecord,
    HistoryValue,
    StoredHistory,
)
from tether.types.history_codec import exchange_source, verify_exchange
from tether.types.storage import IntegrityError, StoreError
from tether.types.transactions import HistoryTurn

from .root import DatabaseRoot

CODEC = "tether-history-v1"


class EnvelopeIntegrity(IntegrityError):
    def __init__(self, bytes_used: int) -> None:
        super().__init__("history_integrity")
        self.bytes_used = bytes_used


class PostgresHistory:
    id = "postgres"

    def __init__(
        self,
        root: DatabaseRoot,
        *,
        transaction_profile: Literal["auto", "shared", "independent"] = "auto",
    ) -> None:
        if transaction_profile not in {"auto", "shared", "independent"}:
            raise ValueError("history_transaction_profile")
        self.transaction_profile = transaction_profile
        self.root = root
        self.capabilities = HistoryCapabilities(
            writable=True,
            externally_recorded=False,
            durability="os",
            delivery="immediate",
            coverage=frozenset({"message", "model_exchange", "summary"}),
        )
        self._table = sql.Identifier(root.schema, "history")

    def for_turn(self, log: TurnLog) -> HistoryProvider:
        if self.transaction_profile == "independent":
            return self
        if isinstance(log, HistoryTurn) and log.history_root is self.root:
            return log.bind_history(self.root)
        if self.transaction_profile == "shared":
            raise StoreError("history_transaction_mismatch")
        return self

    def _ref(self, actor: Actor, session_id: str, key: str, record: HistoryRecord) -> HistoryRef:
        return HistoryRef(
            provider=self.id,
            owner=actor.id,
            session_id=session_id,
            key=key,
            codec=CODEC,
            kind=record.kind,
            sha256=digest(record.model_dump(mode="json")),
        )

    async def put(
        self,
        *,
        actor: Actor,
        session_id: str,
        key: str,
        record: HistoryRecord,
    ) -> HistoryRef:
        self.root.check_loop()
        actor = copy_actor(actor)
        record = HistoryRecord.model_validate(record.model_dump(mode="json"))
        ref = self._ref(actor, session_id, key, record)
        stored = StoredHistory(ref=ref, record=record)
        data = canonical(stored.model_dump(mode="json"))
        if len(data) > self.root.limits.max_batch_bytes:
            raise StoreError("history_write_bytes")

        async def write(connection: AsyncConnection[tuple[object, ...]]) -> HistoryRef:
            await insert_history(connection, self.root, stored)
            return ref

        return await self.root.run(actor, session_id, write, write=True)

    async def read(self, refs: Sequence[HistoryRef], *, actor: Actor) -> tuple[HistoryValue, ...]:
        self.root.check_loop()
        if len(refs) > self.root.limits.page_limit:
            raise StoreError("history_read_limit")
        actor = copy_actor(actor)
        result: list[HistoryValue] = []
        used = 0
        for ref in tuple(refs):
            value, size = await self._read(ref, actor, self.root.limits.max_batch_bytes - used)
            used += size
            result.append(value)
        return tuple(result)

    async def _load(
        self,
        actor: Actor,
        session_id: str,
        key: str,
        maximum: int,
    ) -> tuple[StoredHistory | None, int]:
        async def load(
            connection: AsyncConnection[tuple[object, ...]],
        ) -> tuple[StoredHistory | None, int]:
            cursor = await connection.execute(
                sql.SQL(
                    "SELECT CASE WHEN octet_length(envelope)<=%s THEN envelope END, "
                    "octet_length(envelope), sha256 FROM {} "
                    "WHERE owner=%s AND session=%s AND key=%s"
                ).format(self._table),
                (maximum, actor.id.encode(), session_id.encode(), key.encode()),
            )
            row = await cursor.fetchone()
            if row is None:
                return None, 0
            data, size, sha = row
            if not isinstance(size, int) or size > maximum or not isinstance(data, bytes):
                raise StoreError("history_read_bytes")
            try:
                stored = StoredHistory.model_validate_json(data)
            except ValueError:
                raise EnvelopeIntegrity(size) from None
            if stored.ref.sha256 != sha:
                raise EnvelopeIntegrity(size)
            return stored, size

        return await self.root.run(actor, session_id, load)

    def _scope(self, ref: HistoryRef, actor: Actor) -> HistoryValue | None:
        if ref.owner != actor.id:
            return HistoryValue(ref=ref, status="denied", reason="denied")
        if ref.provider != self.id or ref.codec != CODEC or ref.component != "record":
            return HistoryValue(ref=ref, status="unsupported", reason="unsupported_history_codec")
        return None

    def _checked(self, ref: HistoryRef, actor: Actor, stored: StoredHistory | None) -> HistoryValue:
        if stored is None:
            return HistoryValue(ref=ref, status="missing", reason="history_missing")
        if stored.ref != ref or self._ref(actor, ref.session_id, ref.key, stored.record) != ref:
            return HistoryValue(ref=ref, status="integrity_error", reason="history_integrity")
        return HistoryValue(ref=ref, status="ready", record=stored.record)

    async def _read(self, ref: HistoryRef, actor: Actor, maximum: int) -> tuple[HistoryValue, int]:
        scoped = self._scope(ref, actor)
        if scoped is not None:
            return scoped, 0
        try:
            stored, size = await self._load(actor, ref.session_id, ref.key, maximum)
        except EnvelopeIntegrity as error:
            return HistoryValue(
                ref=ref, status="integrity_error", reason="history_integrity"
            ), error.bytes_used
        except ExecutionDenied:
            return HistoryValue(ref=ref, status="denied", reason="denied"), 0
        except IntegrityError:
            return HistoryValue(ref=ref, status="integrity_error", reason="history_integrity"), 0
        except Exception:
            return HistoryValue(ref=ref, status="unavailable", reason="history_unavailable"), 0
        return self._checked(ref, actor, stored), size

    async def bind_exchange(self, *, actor: Actor, binding: ExchangeBinding) -> ExchangeRefs:
        self.root.check_loop()
        actor = copy_actor(actor)
        if actor.id != binding.owner or binding.codec != "model-port-v1":
            raise StoreError("unsupported_exchange_binding")
        session_id = binding.identity.session_id
        key = f"attempt:{binding.identity.attempt_id}:{binding.codec}:{binding.exchange_sha256}"
        stored, _ = await self._load(actor, session_id, key, self.root.limits.max_batch_bytes)
        if stored is None:
            raise StoreError("exchange_not_observed")
        ref = self._ref(actor, session_id, key, stored.record)
        if stored.ref != ref:
            raise IntegrityError("exchange_integrity")
        verify_exchange(binding, stored.record)
        source = exchange_source(binding, stored.record)
        source_ref = None
        if source is not None:
            source_ref = await self.put(
                actor=copy_actor(actor),
                session_id=session_id,
                key=f"source:{binding.identity.attempt_id}:{binding.source_sha256}",
                record=source,
            )
        return ExchangeRefs(exchange=ref, source=source_ref)


async def insert_history(
    connection: AsyncConnection[tuple[object, ...]], root: DatabaseRoot, stored: StoredHistory
) -> None:
    ref = stored.ref
    data = canonical(stored.model_dump(mode="json"))
    address = (ref.owner.encode(), ref.session_id.encode(), ref.key.encode())
    table = sql.Identifier(root.schema, "history")
    await connection.execute(
        sql.SQL(
            "INSERT INTO {} (owner, session, key, envelope, sha256) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING"
        ).format(table),
        (*address, data, ref.sha256),
    )
    cursor = await connection.execute(
        sql.SQL("SELECT envelope, sha256 FROM {} WHERE owner=%s AND session=%s AND key=%s").format(
            table
        ),
        address,
    )
    if await cursor.fetchone() != (data, ref.sha256):
        raise EntryConflict("history_conflict")
