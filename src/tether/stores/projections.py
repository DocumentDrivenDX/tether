"""Journal-only indexes. Rebuilding these must reproduce every committed projection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from tether.types import TokenUsage
from tether.types.canonical import digest
from tether.types.execution import (
    Entry,
    EntryConflict,
    NewEntry,
    SessionState,
)
from tether.types.storage import IntegrityError, SessionInit, SessionSummary, SessionView, SpecRef

from .indexing import entry_groups, ledger_change, pending_confirmation
from .projection import Projection, finish_summary, new_projection
from .usage import add_usage, usage_change, usage_value


@dataclass
class SessionIndex:
    state: SessionState
    init: SessionInit
    summary: SessionSummary
    entries: list[Entry] = field(default_factory=lambda: list[Entry]())
    keys: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    pending: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    effects: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    sources: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    lookups: dict[tuple[str, str], list[Entry]] = field(
        default_factory=lambda: dict[tuple[str, str], list[Entry]]()
    )
    specs: dict[str, SpecRef] = field(default_factory=lambda: dict[str, SpecRef]())
    counted: dict[str, TokenUsage | None] = field(
        default_factory=lambda: dict[str, TokenUsage | None]()
    )

    missing_usage: int = 0

    def clone(self) -> SessionIndex:
        return replace(
            self,
            entries=self.entries.copy(),
            keys=self.keys.copy(),
            pending=self.pending.copy(),
            effects=self.effects.copy(),
            sources=self.sources.copy(),
            counted=self.counted.copy(),
            lookups={key: values.copy() for key, values in self.lookups.items()},
            specs=self.specs.copy(),
        )

    def append(self, values: tuple[NewEntry, ...], at: datetime) -> tuple[Entry, ...]:
        accepted: list[Entry] = []
        for value in values:
            old = self.keys.get(value.key) if value.key is not None else None
            if old is not None:
                if NewEntry.model_validate(old.model_dump(exclude={"index", "at"})) != value:
                    raise EntryConflict("entry_conflict")
                accepted.append(old)
                continue
            entry = Entry(**value.model_dump(), index=len(self.entries), at=at)
            self.apply(entry)
            accepted.append(entry)
        return tuple(accepted)

    def apply(self, entry: Entry) -> None:
        if entry.index != len(self.entries) or (entry.key is not None and entry.key in self.keys):
            raise IntegrityError("entry_order_invalid")
        self.entries.append(entry)
        if entry.key is not None:
            self.keys[entry.key] = entry
        for manifest in entry.call_manifest:
            self.sources[digest([self.state.id, entry.index, manifest.position])] = entry
        self._index(entry)
        self._facts(entry)
        self.summary = finish_summary(
            self.summary,
            entry,
            pending=len(self.pending),
            effects=len(self.effects),
        )

    def _index(self, entry: Entry) -> None:
        for group in entry_groups(self.state.id, entry):
            self.lookups.setdefault(group, []).append(entry)

    def _facts(self, entry: Entry) -> None:
        projection = Projection(self.state, self.init, self.summary, self.specs)
        projection.metadata(entry)
        self.state, self.init, self.summary = projection.state, projection.init, projection.summary
        change = ledger_change(entry)
        if change is not None:
            name, invocation, present = change
            index = self.pending if name == "pending" else self.effects
            if present:
                index[invocation] = entry
            else:
                index.pop(invocation, None)
        if entry.kind in {"model_call", "model_attempt"}:
            self._usage(entry)

    def _usage(self, entry: Entry) -> None:
        key, value = usage_value(entry)
        change = usage_change(self.counted.get(key), value, exists=key in self.counted)
        self.counted[key] = change.value
        self.missing_usage += change.missing_delta
        totals = add_usage(self.summary.usage, change.added)
        self.summary = self.summary.model_copy(
            update={
                "usage": totals,
                "usage_complete": self.missing_usage == 0,
            }
        )

    def view(self) -> SessionView:
        pending = None
        if self.pending:
            inv, entry = next(iter(self.pending.items()))
            pending = pending_confirmation(self.state.id, inv, entry, self.sources.get(inv))
        return SessionView(
            summary=self.summary, settings=self.init.settings, specs=self.specs, pending=pending
        )


def create_index(session_id: str, owner: str, init: SessionInit, at: datetime) -> SessionIndex:
    projection = new_projection(session_id, owner, init, at)
    return SessionIndex(state=projection.state, init=projection.init, summary=projection.summary)
