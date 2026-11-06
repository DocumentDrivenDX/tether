"""Fixed journal views copy values without copying a whole native prefix per read."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar, cast

import pytest
from pydantic import JsonValue

from tether.stores.jsonl.store import JsonlSessionStore
from tether.stores.jsonl.store import TurnHandle as JsonlTurnHandle
from tether.stores.postgres.capture import Capture
from tether.stores.postgres.handle import TurnHandle as PostgresTurnHandle
from tether.stores.projections import SessionIndex
from tether.types._entry_snapshot import EntrySnapshot, current_history_links, matching_sources
from tether.types.execution import CallManifest, Entry, HistoryRef, NewEntry, SessionState
from tether.types.storage import SessionInit, SessionSummary


def ref(key: str = "source") -> HistoryRef:
    return HistoryRef(
        provider="memory",
        key=key,
        sha256="a" * 64,
        owner="owner",
        session_id="session",
    )


def entry(
    index: int,
    *,
    kind: str = "message",
    history_ref: HistoryRef | None = None,
    facts: dict[str, JsonValue] | None = None,
) -> Entry:
    return Entry(
        kind=kind,
        index=index,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        history_ref=history_ref,
        facts=facts or {},
    )


def append_nested_fact(value: Entry, key: str, item: JsonValue) -> None:
    nested = value.facts.get(key)
    assert isinstance(nested, dict)
    items = nested.get("items")
    assert isinstance(items, list)
    items.append(item)


def append_list_fact(value: Entry, key: str, item: JsonValue) -> None:
    items = value.facts.get(key)
    assert isinstance(items, list)
    items.append(item)


def test_snapshot_is_fixed_and_copies_every_access_path() -> None:
    raw = [entry(0, history_ref=ref(), facts={"nested": {"items": [1]}})]
    snapshot = EntrySnapshot(raw)
    raw.append(entry(1))

    assert len(snapshot) == 1
    assert snapshot[-1].index == 0
    assert snapshot[:1] == (snapshot[0],)

    append_nested_fact(snapshot[0], "nested", 2)
    sliced = snapshot[:]
    append_nested_fact(sliced[0], "nested", 3)
    iterated = next(iter(snapshot))
    append_nested_fact(iterated, "nested", 4)

    assert snapshot[0].facts == {"nested": {"items": [1]}}
    assert snapshot == (raw[0],)
    assert snapshot != [raw[0]]


class ObservedEntry(Entry):
    compared: ClassVar[list[object]] = []

    def __eq__(self, other: object) -> bool:
        self.compared.append(other)
        return super().__eq__(other)


class ObservedHistoryRef(HistoryRef):
    compared: ClassVar[list[object]] = []

    def __eq__(self, other: object) -> bool:
        self.compared.append(other)
        return super().__eq__(other)


class MutatingHistoryRef(HistoryRef):
    compared: ClassVar[list[object]] = []

    def __eq__(self, other: object) -> bool:
        self.compared.append(other)
        if isinstance(other, HistoryRef):
            object.__setattr__(other, "key", "mutated-detached-copy")
        return False


def test_indexed_membership_never_compares_against_raw_accepted_entry() -> None:
    ObservedEntry.compared.clear()
    stored = ObservedEntry(
        kind="message",
        index=4,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        facts={"nested": ["original"]},
        history_ref=ref(),
    )
    snapshot = EntrySnapshot((stored,))
    candidate = entry(4, history_ref=ref(), facts={"nested": ["original"]})

    assert candidate in snapshot
    assert ObservedEntry.compared == []

    append_list_fact(candidate, "nested", "candidate mutation")
    assert candidate not in snapshot
    assert stored.facts == {"nested": ["original"]}


def test_source_matching_detaches_history_reference_before_comparison() -> None:
    ObservedHistoryRef.compared.clear()
    raw_ref = ObservedHistoryRef(**ref().model_dump(mode="python"))
    snapshot = EntrySnapshot((entry(2, history_ref=raw_ref, facts={"nested": [1]}),))

    query_ref = ObservedHistoryRef(**ref().model_dump(mode="python"))
    found = matching_sources(snapshot, query_ref, source_index=2)

    assert len(found) == 1
    assert type(found[0].history_ref) is HistoryRef
    assert len(ObservedHistoryRef.compared) == 1
    assert ObservedHistoryRef.compared[0] is not raw_ref
    assert ObservedHistoryRef.compared[0] is not query_ref
    assert found[0].history_ref == ref()


@pytest.mark.parametrize("as_snapshot", [False, True])
def test_history_reference_subclass_keeps_original_equality_semantics(as_snapshot: bool) -> None:
    base = entry(2, history_ref=ref())
    subclass = ObservedHistoryRef(**ref().model_dump(mode="python"))
    values: Sequence[Entry] = (base,)
    selected: Sequence[Entry] = EntrySnapshot(values) if as_snapshot else values

    assert ref() != subclass
    assert matching_sources(selected, subclass, source_index=2) == ()


def test_hostile_history_comparison_can_mutate_only_detached_references() -> None:
    MutatingHistoryRef.compared.clear()
    raw_ref = MutatingHistoryRef(**ref().model_dump(mode="python"))
    source = Entry.model_construct(
        kind="message",
        index=2,
        at=datetime(2026, 1, 1, tzinfo=UTC),
        history_ref=raw_ref,
        facts={"nested": [1]},
        call_manifest=(),
        turn_id=None,
        key=None,
    )
    snapshot = EntrySnapshot((source,))
    query_ref = ref()

    assert matching_sources(snapshot, query_ref, source_index=2) == ()
    assert raw_ref.key == "source"
    assert query_ref.key == "source"
    assert len(MutatingHistoryRef.compared) == 1
    assert MutatingHistoryRef.compared[0] is not raw_ref
    assert MutatingHistoryRef.compared[0] is not query_ref


class MutatingCandidate:
    def __init__(self) -> None:
        self.compared: list[object] = []

    def __eq__(self, other: object) -> bool:
        self.compared.append(other)
        if isinstance(other, Entry):
            append_nested_fact(other, "nested", "candidate mutation")
        return False


def test_subclass_and_hostile_membership_preserve_tuple_comparison_semantics() -> None:
    raw = entry(4, facts={"nested": {"items": [1]}})
    snapshot = EntrySnapshot((raw,))
    subclass = ObservedEntry(**raw.model_dump(mode="python"))
    hostile = MutatingCandidate()

    assert subclass not in (raw,)
    assert (subclass in snapshot) is False
    assert hostile not in snapshot
    assert len(hostile.compared) == 1
    observed = hostile.compared[0]
    assert isinstance(observed, Entry)
    assert observed is not raw
    assert snapshot[0].facts == {"nested": {"items": [1]}}


def test_native_append_detaches_input_and_preserves_unknown_extension_json() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = SessionState(id="session", owner="owner")
    summary = SessionSummary(id="session", owner="owner", created_at=now, updated_at=now)
    index = SessionIndex(state=state, init=SessionInit(), summary=summary)
    value = NewEntry(
        kind="future.extension.kind",
        key="future-entry",
        history_ref=ref(),
        call_manifest=(
            CallManifest(
                position=0,
                call_id="future-call",
                tool="future-tool",
                args_digest="a" * 64,
                tool_digest="b" * 64,
                effect="read_only",
                replay="safe",
            ),
        ),
        facts={
            "nested": {"items": [None, True, 2**80, {"extension": "preserved"}]},
            "unknown": ["value"],
        },
    )
    accepted = index.append((value,), now)[0]
    snapshot = EntrySnapshot(index.entries)
    expected = accepted.model_dump(mode="python")

    nested = cast(dict[str, JsonValue], value.facts["nested"])
    input_items = cast(list[JsonValue], nested["items"])
    input_items.append("caller changed input")
    index.append((NewEntry(kind="future.second.kind"),), now)
    copied = snapshot[0]
    assert len(snapshot) == 1
    assert copied.model_dump(mode="python") == expected
    assert copied.kind == "future.extension.kind"

    assert copied.history_ref is not None
    changed_ref = copied.history_ref.model_copy(update={"key": "returned-copy-only"})
    changed = copied.model_copy(update={"history_ref": changed_ref})
    assert changed.history_ref is not None
    assert changed.history_ref.key == "returned-copy-only"
    assert snapshot[0].history_ref == ref()


def test_helpers_preserve_duplicates_order_and_nested_copy_isolation() -> None:
    source_ref = ref()
    values = (
        entry(2, history_ref=source_ref, facts={"source": [1]}),
        entry(7, history_ref=source_ref, facts={"source": [2]}),
        entry(8, kind="history_linked", facts={"message_index": 2, "payload": [3]}),
        entry(9, kind="history_linked", facts={"message_index": 2, "payload": [4]}),
        entry(10, kind="history_linked", facts={"message_index": 7, "payload": [5]}),
    )
    snapshot = EntrySnapshot(values)

    assert [item.index for item in matching_sources(snapshot, source_ref)] == [2, 7]
    matches = matching_sources(snapshot, source_ref, source_index=2)
    assert [item.index for item in matches] == [2]
    append_list_fact(matches[0], "source", 6)
    assert snapshot[0].facts == {"source": [1]}

    links = current_history_links(snapshot, 2)
    assert [item.index for item in links] == [8, 9]
    append_list_fact(links[0], "payload", 7)
    assert snapshot[2].facts == {"message_index": 2, "payload": [3]}


def test_dense_prefix_uses_direct_candidates_without_group_maps() -> None:
    source_ref = ref()
    values = tuple(entry(index, history_ref=source_ref) for index in range(5))
    snapshot = EntrySnapshot(values)

    assert [item.index for item in matching_sources(snapshot, source_ref, source_index=3)] == [3]
    assert values[3] in snapshot
    assert snapshot._dense_indices is True
    assert snapshot._by_index is None
    assert snapshot._links_by_message is None


def test_dense_exact_integer_queries_reject_negative_and_out_of_range_indices() -> None:
    source_ref = ref()
    snapshot = EntrySnapshot(tuple(entry(index, history_ref=source_ref) for index in range(3)))

    assert matching_sources(snapshot, source_ref, source_index=-1) == ()
    assert matching_sources(snapshot, source_ref, source_index=3) == ()
    assert current_history_links(snapshot, -1) == ()
    assert current_history_links(snapshot, 3) == ()
    assert entry(-1, history_ref=source_ref) not in snapshot


@pytest.mark.parametrize("as_snapshot", [False, True])
def test_dense_history_links_use_message_index_and_keep_all_matching_rows(
    as_snapshot: bool,
) -> None:
    values: Sequence[Entry] = (
        entry(0, facts={"message": "first"}),
        entry(1, kind="history_linked", facts={"message_index": 0, "link": [1]}),
        entry(2, kind="history_linked", facts={"message_index": 0, "link": [2]}),
        entry(3, facts={"message": "second"}),
        entry(4, kind="history_linked", facts={"message_index": 3, "link": [3]}),
        entry(5, kind="history_linked", facts={"message_index": 99, "link": [4]}),
    )
    selected: Sequence[Entry] = EntrySnapshot(values) if as_snapshot else values

    assert [item.index for item in current_history_links(selected, 0)] == [1, 2]
    assert [item.index for item in current_history_links(selected, 3)] == [4]
    assert [item.index for item in current_history_links(selected, 99)] == [5]
    assert current_history_links(selected, 1) == ()
    assert current_history_links(selected, 2) == ()


def test_sparse_prefix_fallback_preserves_duplicates_and_index_coercion() -> None:
    source_ref = ref()
    values = (
        entry(1, history_ref=source_ref),
        entry(1, history_ref=source_ref),
        entry(0, kind="history_linked", facts={"message_index": 1}),
        entry(2, kind="history_linked", facts={"message_index": 1.0}),
    )
    snapshot = EntrySnapshot(values)
    custom_index = type("IndexLike", (int,), {})(1)
    float_index = cast(int, 1.0)

    assert snapshot._has_dense_indices() is False
    assert [item.index for item in matching_sources(snapshot, source_ref, source_index=True)] == [
        1,
        1,
    ]
    float_matches = matching_sources(snapshot, source_ref, source_index=float_index)
    assert [item.index for item in float_matches] == [1, 1]
    assert [
        item.index for item in matching_sources(snapshot, source_ref, source_index=custom_index)
    ] == [1, 1]
    assert [item.index for item in current_history_links(snapshot, 1)] == [0, 2]
    assert [item.index for item in current_history_links(snapshot, True)] == [0, 2]
    assert [item.index for item in current_history_links(snapshot, float_index)] == [0, 2]


def test_generic_sequences_preserve_duplicate_filter_semantics() -> None:
    source_ref = ref()
    values: Sequence[Entry] = (
        entry(3, history_ref=source_ref),
        entry(3, history_ref=source_ref),
        entry(4, kind="history_linked", facts={"message_index": 3}),
        entry(5, kind="history_linked", facts={"message_index": 3}),
        entry(6, kind="history_linked", facts={"message_index": 4}),
    )

    assert len(matching_sources(values, source_ref, source_index=3)) == 2
    assert [item.index for item in current_history_links(values, 3)] == [4, 5]


@pytest.mark.parametrize("backend", ["jsonl", "postgres"])
def test_native_handles_return_fixed_isolated_prefix(backend: str) -> None:
    raw = [entry(0, facts={"nested": {"items": [1]}})]
    if backend == "jsonl":
        fake_store = SimpleNamespace(owned=lambda *_: SimpleNamespace(entries=raw))
        handle = JsonlTurnHandle(cast(JsonlSessionStore, fake_store), "session", "owner", 1, "turn")
    else:
        fake_store = SimpleNamespace(root=SimpleNamespace(check_loop=lambda: None))
        handle = object.__new__(PostgresTurnHandle)
        handle._store = cast(Capture, fake_store)
        handle._index = cast(SessionIndex, SimpleNamespace(entries=raw))

    view = handle.entries
    assert isinstance(view, EntrySnapshot)
    raw.append(entry(1))
    assert len(view) == 1
    append_nested_fact(view[0], "nested", 2)
    assert handle.entries[0].facts == {"nested": {"items": [1]}}
