"""Indexed authoritative JSONL history over the shared single-writer root."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from tether.types.canonical import canonical, digest
from tether.types.execution import Actor, EntryConflict, HistoryRef
from tether.types.history import (
    ExchangeBinding,
    ExchangeRefs,
    HistoryCapabilities,
    HistoryRecord,
    HistoryValue,
    StoredHistory,
)
from tether.types.history_codec import exchange_source, verify_exchange
from tether.types.storage import IntegrityError, JournalFiles, StoreError

CODEC = "tether-history-v1"


class JsonlHistory:
    id = "jsonl"

    def __init__(self, files: JournalFiles) -> None:
        self.files = files
        self.capabilities = HistoryCapabilities(
            writable=True,
            externally_recorded=False,
            durability=files.durability,
            delivery="immediate",
            coverage=frozenset({"message", "model_exchange", "summary"}),
        )
        self._index: dict[tuple[str, str, str], tuple[HistoryRef, int]] = {}
        self._lock = asyncio.Lock()
        self._index_bytes = 0

    @classmethod
    async def open(cls, files: JournalFiles) -> JsonlHistory:
        files.claim("history")
        history = cls(files)
        async for item in files.scan("history"):
            try:
                stored = StoredHistory.model_validate(item.value)
                history._verify(stored)
                key = (stored.ref.owner, stored.ref.session_id, stored.ref.key)
                if key in history._index:
                    raise IntegrityError("duplicate_history_key")
                history._account(stored.ref)
                history._index[key] = (stored.ref, item.offset)
            except ValueError:
                raise IntegrityError("history_integrity") from None
        return history

    def _account(self, ref: HistoryRef, *, commit: bool = True) -> None:
        size = len(canonical(ref.model_dump(mode="json")))
        if self._index_bytes + size > self.files.limits.max_index_bytes:
            raise StoreError("history_index_limit")
        if commit:
            self._index_bytes += size

    @staticmethod
    def _verify(stored: StoredHistory) -> None:
        if (
            stored.ref.codec != CODEC
            or stored.ref.provider != "jsonl"
            or stored.ref.kind != stored.record.kind
            or stored.ref.component != "record"
            or stored.ref.sha256 != digest(stored.record.model_dump(mode="json"))
        ):
            raise IntegrityError("history_integrity")

    async def put(
        self, *, actor: Actor, session_id: str, key: str, record: HistoryRecord
    ) -> HistoryRef:
        record = HistoryRecord.model_validate(record.model_dump(mode="json"))
        ref = HistoryRef(
            provider=self.id,
            key=key,
            owner=actor.id,
            session_id=session_id,
            codec=CODEC,
            kind=record.kind,
            sha256=digest(record.model_dump(mode="json")),
        )
        address = (actor.id, session_id, key)
        async with self._lock:
            if address in self._index:
                existing, _ = self._index[address]
                if existing != ref:
                    raise EntryConflict("history_conflict")
                return ref
            self._account(ref, commit=False)
            stored = StoredHistory(ref=ref, record=record)
            offset = await self.files.append_file("history", stored.model_dump(mode="json"))
            self._account(ref)
            self._index[address] = (ref, offset)
        return ref

    async def read(self, refs: Sequence[HistoryRef], *, actor: Actor) -> tuple[HistoryValue, ...]:
        if len(refs) > self.files.limits.page_limit:
            raise StoreError("history_read_limit")
        return tuple([await self._read(ref, actor) for ref in refs])

    async def _read(self, ref: HistoryRef, actor: Actor) -> HistoryValue:
        if actor.id != ref.owner:
            return HistoryValue(ref=ref, status="denied", reason="denied")
        if ref.codec != CODEC or ref.provider != self.id or ref.component != "record":
            return HistoryValue(ref=ref, status="unsupported", reason="unsupported_history_codec")
        indexed = self._index.get((actor.id, ref.session_id, ref.key))
        if indexed is None:
            return HistoryValue(ref=ref, status="missing", reason="history_missing")
        expected, offset = indexed
        if expected != ref:
            return HistoryValue(ref=ref, status="integrity_error", reason="history_integrity")
        return await self._load(ref, offset)

    async def _load(self, ref: HistoryRef, offset: int) -> HistoryValue:
        try:
            stored = StoredHistory.model_validate(await self.files.read_file("history", offset))
            self._verify(stored)
            if stored.ref != ref:
                raise IntegrityError("history_integrity")
        except (ValueError, IntegrityError):
            return HistoryValue(ref=ref, status="integrity_error", reason="history_integrity")
        except (OSError, StoreError):
            return HistoryValue(ref=ref, status="unavailable", reason="history_unavailable")
        return HistoryValue(ref=ref, status="ready", record=stored.record)

    async def bind_exchange(self, *, actor: Actor, binding: ExchangeBinding) -> ExchangeRefs:
        if actor.id != binding.owner or binding.codec != "model-port-v1":
            raise StoreError("unsupported_exchange_binding")
        key = f"attempt:{binding.identity.attempt_id}:{binding.codec}:{binding.exchange_sha256}"
        indexed = self._index.get((actor.id, binding.identity.session_id, key))
        if indexed is None:
            raise StoreError("exchange_not_observed")
        ref, _ = indexed
        value = await self._read(ref, actor)
        record = value.record
        if record is None or ref.sha256 != binding.exchange_sha256:
            raise IntegrityError("exchange_integrity")
        verify_exchange(binding, record)
        source = await self._source(actor, binding, record)
        return ExchangeRefs(exchange=ref, source=source)

    async def _source(
        self, actor: Actor, binding: ExchangeBinding, record: HistoryRecord
    ) -> HistoryRef | None:
        source = exchange_source(binding, record)
        if source is None:
            return None
        return await self.put(
            actor=actor,
            session_id=binding.identity.session_id,
            key=f"source:{binding.identity.attempt_id}:{binding.source_sha256}",
            record=source,
        )
