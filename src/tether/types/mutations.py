"""Optional atomic metadata receipts; content-bearing results never become audit packets."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from tether.types import Payload, PositiveInt
from tether.types.execution import Entry
from tether.types.storage import (
    SessionInit,
    SessionPatch,
    SessionStore,
    SessionSummary,
    Unsupported,
)

MUTATION_RECEIPT_VERSION = 1
Version = Annotated[int, Field(strict=True, ge=1, le=1)]


class MutationCommit(Payload):
    owner: str
    session_id: str
    seq: PositiveInt
    entries: tuple[Entry, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def metadata_only(self) -> Self:
        for entry in self.entries:
            settings = entry.kind == "settings" and isinstance(entry.facts.get("changes"), dict)
            event = entry.facts.get("event")
            session = (
                entry.kind == "session" and isinstance(event, str) and event in {"titled", "tagged"}
            )
            if not (settings or session) or entry.history_ref is not None or entry.call_manifest:
                raise ValueError("mutation_entries_invalid")
        return self


def match(summary: SessionSummary, commit: MutationCommit) -> None:
    if (summary.owner, summary.id, summary.last_seq) != (
        commit.owner,
        commit.session_id,
        commit.seq,
    ):
        raise ValueError("mutation_scope_invalid")


class CreateReceipt(Payload):
    version: Version = MUTATION_RECEIPT_VERSION
    summary: SessionSummary
    disposition: Literal["created", "existing"]
    commit: MutationCommit | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.disposition == "existing":
            if self.commit is not None:
                raise ValueError("mutation_disposition_invalid")
        else:
            if self.commit is None or self.commit.entries or self.summary.entry_count != 0:
                raise ValueError("mutation_disposition_invalid")
            match(self.summary, self.commit)
        return self


class UpdateReceipt(Payload):
    version: Version = MUTATION_RECEIPT_VERSION
    summary: SessionSummary
    disposition: Literal["accepted", "noop"]
    commit: MutationCommit | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.disposition == "noop":
            if self.commit is not None:
                raise ValueError("mutation_disposition_invalid")
        else:
            if self.commit is None or not self.commit.entries:
                raise ValueError("mutation_disposition_invalid")
            match(self.summary, self.commit)
            start = self.summary.entry_count - len(self.commit.entries)
            if start < 0 or tuple(e.index for e in self.commit.entries) != tuple(
                range(start, self.summary.entry_count)
            ):
                raise ValueError("mutation_positions_invalid")
        return self


@runtime_checkable
class MutationReceipts(Protocol):
    async def create_with_receipt(self, owner: str, init: SessionInit) -> CreateReceipt: ...
    async def update_with_receipt(
        self, session_id: str, owner: str, patch: SessionPatch
    ) -> UpdateReceipt: ...


def receipt_store(store: SessionStore) -> MutationReceipts | None:
    version = store.capabilities.mutation_receipts
    if type(version) is not int or version not in (0, MUTATION_RECEIPT_VERSION):
        raise Unsupported("mutation_receipt_version_invalid")
    if version == 0:
        return None
    if (
        not isinstance(store, MutationReceipts)
        or not callable(store.create_with_receipt)
        or not callable(store.update_with_receipt)
    ):
        raise Unsupported("mutation_receipts_unavailable")
    return store


def created(summary: SessionSummary) -> CreateReceipt:
    return CreateReceipt(
        summary=summary.model_copy(deep=True),
        disposition="created",
        commit=MutationCommit(owner=summary.owner, session_id=summary.id, seq=summary.last_seq),
    )


def updated(summary: SessionSummary, entries: tuple[Entry, ...]) -> UpdateReceipt:
    commit = (
        MutationCommit(
            owner=summary.owner,
            session_id=summary.id,
            seq=summary.last_seq,
            entries=tuple(e.model_copy(deep=True) for e in entries),
        )
        if entries
        else None
    )
    return UpdateReceipt(
        summary=summary.model_copy(deep=True),
        disposition="accepted" if entries else "noop",
        commit=commit,
    )
