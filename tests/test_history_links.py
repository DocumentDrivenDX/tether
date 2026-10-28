"""Accepted history links select one bounded, journal-pinned replacement."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tether.engine.governance import AllowAll
from tether.history.jsonl import JsonlHistory
from tether.sessions.access import SessionAccess
from tether.sessions.links import bind
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types import Message
from tether.types.execution import Actor, CommitUnknown, Entry, HistoryRef, NewEntry
from tether.types.history import HistoryLink, HistoryRecord
from tether.types.history_links import select
from tether.types.storage import IntegrityError, SessionInit


def ref(key: str) -> HistoryRef:
    return HistoryRef(
        provider="fabricated",
        key=key,
        sha256="a" * 64,
        owner="owner",
        session_id="session",
        kind="message",
    )


def linked(index: int, previous: HistoryRef, verified: HistoryRef) -> Entry:
    link = HistoryLink(
        message_index=0,
        previous=previous,
        verified=verified,
        canonical_digest=previous.sha256,
    )
    return Entry(
        kind="history_linked",
        index=index,
        at=datetime.now(UTC),
        facts=link.model_dump(mode="json"),
    )


def test_latest_accepted_link_and_pinned_head_select_one_chain():
    original, first, second, third = (ref(name) for name in ("old", "first", "second", "third"))
    entries = (
        linked(1, original, first),
        linked(2, first, second),
        linked(3, original, third),
    )
    assert select(original, 0, entries, through_index=1, max_hops=3) == first
    assert select(original, 0, entries, through_index=2, max_hops=3) == second
    assert select(original, 0, entries, through_index=3, max_hops=3) == third
    assert select(original, 0, entries, through_index=3, max_hops=1) == third


def test_history_link_cycle_hop_limit_and_conflicting_equivalence_refuse():
    original, first, second = ref("old"), ref("first"), ref("second")
    chain = (linked(1, original, first), linked(2, first, second))
    with pytest.raises(IntegrityError, match="history_link_hops"):
        select(original, 0, chain, through_index=2, max_hops=1)
    with pytest.raises(IntegrityError, match="history_link_cycle"):
        select(original, 0, (*chain, linked(3, second, original)), through_index=3, max_hops=4)
    facts = json.loads(json.dumps(chain[0].facts))
    facts["verified"]["sha256"] = "b" * 64
    bad = chain[0].model_copy(update={"facts": facts})
    with pytest.raises(IntegrityError, match="history_link_integrity"):
        select(original, 0, (bad,), through_index=1, max_hops=4)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("owner", "other"),
        ("session_id", "other"),
        ("kind", "summary"),
        ("component", "assistant"),
        ("codec", "other-codec"),
        ("sha256", "b" * 64),
    ],
)
def test_link_refuses_cross_scope_or_unqualified_semantic_codec(field, changed):
    original = ref("old")
    replacement = ref("new").model_copy(update={field: changed})
    with pytest.raises(ValueError, match="history_link_equivalence"):
        HistoryLink(
            message_index=0,
            previous=original,
            verified=replacement,
            canonical_digest=original.sha256,
        )


def test_unsupported_history_link_version_is_not_selected():
    original, replacement = ref("old"), ref("new")
    entry = linked(1, original, replacement)
    facts = json.loads(json.dumps(entry.facts))
    facts["schema_version"] = 2
    with pytest.raises(IntegrityError, match="history_link_integrity"):
        select(
            original,
            0,
            (entry.model_copy(update={"facts": facts}),),
            through_index=1,
            max_hops=8,
        )


@pytest.mark.anyio
async def test_unknown_link_ack_reopens_and_retries_original_key_without_duplicate(
    tmp_path, client_limits, monkeypatch
):
    actor = Actor(id="owner")
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    history = await JsonlHistory.open(root)
    session = await store.create(actor.id, SessionInit())
    record = HistoryRecord(kind="message", message=Message(role="user", content="Fabricated"))
    original = await history.put(actor=actor, session_id=session.id, key="original", record=record)
    replacement = await history.put(
        actor=actor, session_id=session.id, key="replacement", record=record
    )
    async with store.turn(session.id, actor.id, turn_id="source") as log:
        await log.append((NewEntry(kind="message", history_ref=original),))
    access = SessionAccess(store, history, AllowAll(), client_limits)
    access.bind()
    append = store.append

    async def lose_ack(sid, owner, generation, entries):
        accepted = await append(sid, owner, generation, entries)
        if any(entry.kind == "history_linked" for entry in entries):
            raise CommitUnknown("fabricated_link_ack_loss")
        return accepted

    with monkeypatch.context() as scope:
        scope.setattr(store, "append", lose_ack)
        with pytest.raises(CommitUnknown, match="fabricated_link_ack_loss"):
            await bind(
                access,
                actor,
                session.id,
                0,
                original,
                replacement,
                key="stable-link-key",
            )
    assert (await history.read((original,), actor=actor))[0].record == record
    assert await root.aclose()
    root = await JsonlRoot.open(tmp_path)
    try:
        store = await JsonlSessionStore.open(
            root, clock=lambda: datetime.now(UTC) + timedelta(minutes=1)
        )
        history = await JsonlHistory.open(root)
        access = SessionAccess(store, history, AllowAll(), client_limits)
        access.bind()
        accepted = await bind(
            access,
            actor,
            session.id,
            0,
            original,
            replacement,
            key="stable-link-key",
        )
        assert accepted.kind == "history_linked"
        view = await store.get(session.id, actor.id)
        assert view is not None
        assert view.summary.entry_count == 2
    finally:
        assert await root.aclose()
