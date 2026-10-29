"""Bounded turn-local content, enlisted only with its accepting journal batch."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from tether.types.canonical import canonical
from tether.types.execution import Actor, EntryConflict, ExecutionDenied, HistoryRef, NewEntry
from tether.types.history import HistoryRecord, StoredHistory
from tether.types.storage import StoreError

from .history import PostgresHistory
from .root import DatabaseRoot


class PairedLog(Protocol):
    def check_history(self, actor: Actor, session_id: str) -> None: ...


class PairedHistory(PostgresHistory):
    def __init__(self, root: DatabaseRoot, log: PairedLog) -> None:
        super().__init__(root, transaction_profile="shared")
        self.log = log
        self._pending: dict[str, StoredHistory] = {}
        self._bytes = 0
        self.log_owner: str | None = None
        self.log_session: str | None = None

    def _scope(self, ref: HistoryRef, actor: Actor):
        self.log.check_history(actor, ref.session_id)
        return super()._scope(ref, actor)

    async def put(
        self, *, actor: Actor, session_id: str, key: str, record: HistoryRecord
    ) -> HistoryRef:
        self.log.check_history(actor, session_id)
        self.log_owner, self.log_session = actor.id, session_id
        record = HistoryRecord.model_validate(record.model_dump(mode="json"))
        ref = self._ref(actor, session_id, key, record)
        stored = StoredHistory(ref=ref, record=record)
        previous = self._pending.get(key)
        if previous is not None:
            if previous != stored:
                raise EntryConflict("history_conflict")
            return ref
        size = len(canonical(stored.model_dump(mode="json")))
        limits = self.root.limits
        if len(self._pending) >= limits.page_limit or self._bytes + size > limits.max_batch_bytes:
            raise StoreError("history_staging_limit")
        self._pending[key] = stored
        self._bytes += size
        return ref

    async def _load(
        self, actor: Actor, session_id: str, key: str, maximum: int
    ) -> tuple[StoredHistory | None, int]:
        self.log.check_history(actor, session_id)
        pending = self._pending.get(key)
        if pending is not None:
            size = len(canonical(pending.model_dump(mode="json")))
            if size > maximum:
                raise StoreError("history_read_bytes")
            return pending.model_copy(deep=True), size
        return await super()._load(actor, session_id, key, maximum)

    def select(self, entries: Sequence[NewEntry]) -> tuple[StoredHistory, ...]:
        refs = tuple(ref for entry in entries for ref in references(entry))
        selected: list[StoredHistory] = []
        for ref in refs:
            if (
                ref.provider != self.id
                or ref.owner != self.log_owner
                or ref.session_id != self.log_session
            ):
                continue
            pending = self._pending.get(ref.key)
            if pending is not None:
                if pending.ref != ref:
                    raise ExecutionDenied("history_staging_binding")
                selected.append(pending.model_copy(deep=True))
        return tuple({stored.ref.key: stored for stored in selected}.values())

    def accepted(self, records: Sequence[StoredHistory]) -> None:
        for stored in records:
            if self._pending.get(stored.ref.key) != stored:
                raise ExecutionDenied("history_staging_binding")
            self._pending.pop(stored.ref.key)
            self._bytes -= len(canonical(stored.model_dump(mode="json")))


def references(entry: NewEntry) -> tuple[HistoryRef, ...]:
    result = [entry.history_ref] if entry.history_ref is not None else []
    fields = {
        "model_attempt": ("history_ref",),
        "model_receipt": ("history_ref",),
        "model_call": ("exchange_ref",),
        "compaction": ("summary_ref",),
    }.get(entry.kind, ())
    for field in fields:
        raw = entry.facts.get(field)
        if raw is not None:
            result.append(HistoryRef.model_validate(raw))
    if entry.kind == "preparation":
        raw_refs = entry.facts.get("references", [])
        if not isinstance(raw_refs, list):
            raise ExecutionDenied("history_staging_binding")
        result.extend(HistoryRef.model_validate(raw) for raw in raw_refs)
    if entry.kind == "settlement":
        raw_entries = entry.facts.get("result_entries", [])
        if not isinstance(raw_entries, list):
            raise ExecutionDenied("history_staging_binding")
        for raw in raw_entries:
            ref = NewEntry.model_validate(raw).history_ref
            if ref is not None:
                result.append(ref)
    return tuple(result)
