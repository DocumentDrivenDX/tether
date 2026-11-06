"""Private fixed-prefix view over store-owned journal entries.

Native stores pass their private append-only entry prefix. The snapshot retains
only those references privately and copies every value crossing its boundary.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import overload

from tether.types.execution import Entry, HistoryRef


def _entry_copy(entry: Entry) -> Entry:
    return Entry.model_validate(entry.model_dump(mode="python"))


def _ref_copy(ref: HistoryRef) -> HistoryRef:
    return ref.model_copy(deep=True)


class EntrySnapshot(Sequence[Entry]):
    """A stable accepted prefix with copy-on-access semantics."""

    __slots__ = ("_by_index", "_dense_indices", "_links_by_message", "_raw")

    def __init__(self, entries: Sequence[Entry]) -> None:
        # The backing sequence is an append-only native store-owned prefix. A tuple
        # fixes the head without deep-copying every accepted fact at each read.
        self._raw = tuple(entries)
        self._by_index: dict[int, tuple[Entry, ...]] | None = None
        self._dense_indices: bool | None = None
        self._links_by_message: dict[int | float, tuple[Entry, ...]] | None = None

    def __len__(self) -> int:
        return len(self._raw)

    @overload
    def __getitem__(self, index: int) -> Entry: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[Entry, ...]: ...

    def __getitem__(self, index: int | slice) -> Entry | tuple[Entry, ...]:
        if isinstance(index, slice):
            return tuple(_entry_copy(entry) for entry in self._raw[index])
        return _entry_copy(self._raw[index])

    def __iter__(self) -> Iterator[Entry]:
        for entry in self._raw:
            yield _entry_copy(entry)

    def __contains__(self, candidate: object) -> bool:
        if type(candidate) is Entry:
            # Detach before equality: a caller-defined Entry subclass must never
            # receive a raw accepted object from the store.
            safe = candidate.model_copy(deep=True)
            if type(safe.index) is int and self._has_dense_indices():
                if safe.index < 0 or safe.index >= len(self._raw):
                    return False
                return _entry_copy(self._raw[safe.index]) == safe
            return any(_entry_copy(entry) == safe for entry in self._indices().get(safe.index, ()))
        return any(entry == candidate for entry in self)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EntrySnapshot):
            return tuple(self) == tuple(other)
        if isinstance(other, tuple):
            return tuple(self) == other
        return False

    def _indices(self) -> dict[int, tuple[Entry, ...]]:
        if self._by_index is None:
            grouped: dict[int, list[Entry]] = {}
            for entry in self._raw:
                if type(entry.index) is int:
                    grouped.setdefault(entry.index, []).append(entry)
            self._by_index = {key: tuple(values) for key, values in grouped.items()}
        return self._by_index

    def _has_dense_indices(self) -> bool:
        if self._dense_indices is None:
            self._dense_indices = all(
                type(entry.index) is int and entry.index == position
                for position, entry in enumerate(self._raw)
            )
        return self._dense_indices

    def _links(self) -> dict[int | float, tuple[Entry, ...]]:
        if self._links_by_message is None:
            grouped: dict[int | float, list[Entry]] = {}
            for entry in self._raw:
                if entry.kind != "history_linked":
                    continue
                message_index = entry.facts.get("message_index")
                if isinstance(message_index, (int, float)):
                    grouped.setdefault(message_index, []).append(entry)
            self._links_by_message = {key: tuple(values) for key, values in grouped.items()}
        return self._links_by_message

    def matching_sources(self, ref: HistoryRef, source_index: int | None) -> tuple[Entry, ...]:
        expected = _ref_copy(ref)
        if source_index is None:
            candidates = self._raw
        elif type(source_index) is int and self._has_dense_indices():
            if source_index < 0 or source_index >= len(self._raw):
                return ()
            candidate = self._raw[source_index]
            candidates = (candidate,) if candidate.index == source_index else ()
        else:
            candidates = self._indices().get(source_index, ())
        result: list[Entry] = []
        for entry in candidates:
            if source_index is not None and entry.index != source_index:
                continue
            actual = entry.history_ref
            if actual is not None and _ref_copy(actual) == expected:
                result.append(_entry_copy(entry))
        return tuple(result)

    def current_history_links(self, source_index: int) -> tuple[Entry, ...]:
        return tuple(
            _entry_copy(entry)
            for entry in self._links().get(source_index, ())
            if entry.kind == "history_linked" and entry.facts.get("message_index") == source_index
        )


def matching_sources(
    entries: Sequence[Entry], ref: HistoryRef, *, source_index: int | None = None
) -> tuple[Entry, ...]:
    """Return every matching source, preserving duplicate detection and order."""

    if isinstance(entries, EntrySnapshot):
        return entries.matching_sources(ref, source_index)
    expected = _ref_copy(ref)
    result: list[Entry] = []
    for entry in entries:
        if source_index is not None and entry.index != source_index:
            continue
        actual = entry.history_ref
        if actual is not None and _ref_copy(actual) == expected:
            result.append(_entry_copy(entry))
    return tuple(result)


def current_history_links(entries: Sequence[Entry], source_index: int) -> tuple[Entry, ...]:
    """Return matching history-link rows as detached values in journal order."""

    if isinstance(entries, EntrySnapshot):
        return entries.current_history_links(source_index)
    return tuple(
        _entry_copy(entry)
        for entry in entries
        if entry.kind == "history_linked" and entry.facts.get("message_index") == source_index
    )
