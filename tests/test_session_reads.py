"""Store Client surfaces preserve owner scope, cursor bounds and feed retention."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.execution import NewEntry
from tether.types.storage import (
    ChangeGap,
    SessionInit,
    SessionPatch,
    SessionQuery,
    StorageLimits,
    TurnInProgress,
    UnknownSession,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(params=["memory", "jsonl"])
async def store(request, tmp_path):
    files = MemoryFiles() if request.param == "memory" else await JsonlRoot.open(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def clock():
        nonlocal now
        now += timedelta(seconds=1)
        return now

    value = await JsonlSessionStore.open(files, clock=clock)
    yield value
    if isinstance(files, JsonlRoot):
        assert await files.aclose()


async def test_keyset_query_scope_and_metadata_updates(store):
    sessions = [
        await store.create(
            "owner", SessionInit(ref=str(i), title=f"Fabricated {i}", tags=("selected",))
        )
        for i in range(3)
    ]
    await store.create("other", SessionInit(ref="0", title="Private fabricated"))
    query = SessionQuery(owner="owner", order="created_asc", tags_all=("selected",))
    first = await store.list(query, limit=2)
    assert [s.id for s in first.items] == [s.id for s in sessions[:2]]
    assert first.next
    tail = await store.list(query, cursor=first.next, limit=2)
    assert [s.id for s in tail.items] == [sessions[2].id]
    assert tail.next is None
    for changed in (
        query.model_copy(update={"owner": "other"}),
        query.model_copy(update={"order": "updated_desc"}),
    ):
        with pytest.raises(ValueError, match="invalid_cursor"):
            await store.list(changed, cursor=first.next, limit=2)
    assert await store.find("missing", "0") is None
    assert (await store.find("owner", "0")).id == sessions[0].id
    assert await store.get(sessions[0].id, "other") is None
    updated = await store.update(
        sessions[0].id,
        "owner",
        SessionPatch(
            title="Revised", tags=(), settings={"approval": "manual", "x.fabricated": True}
        ),
    )
    assert updated.title == "Revised"
    assert updated.tags == ()
    assert (await store.get(updated.id, "owner")).settings["approval"] == "manual"
    selected = await store.list(SessionQuery(owner="owner", text="revised"), limit=5)
    assert [s.id for s in selected.items] == [updated.id]
    with pytest.raises(TurnInProgress):
        async with store.turn(updated.id, "owner", turn_id="held"):
            await store.update(updated.id, "owner", SessionPatch(title="Rejected"))
    assert (await store.get(updated.id, "owner")).summary.title == "Revised"


async def test_effect_page_is_indexed_and_resolutions_update_it(store):
    session = await store.create("owner", SessionInit())
    async with store.turn(session.id, "owner", turn_id="turn") as log:
        await log.append(
            [
                NewEntry(
                    kind="tool_call",
                    facts={
                        "invocation_id": str(i),
                        "status": "started",
                        "effect": "effectful",
                        "tool": "fabricated",
                    },
                )
                for i in range(3)
            ]
        )
        await log.append(
            [NewEntry(kind="resolved", facts={"invocation_id": "1", "outcome": "not_completed"})]
        )
    page = await store.effects(session.id, "owner", limit=1)
    assert [e.invocation_id for e in page.items] == ["0"]
    assert page.next_start == 2
    tail = await store.effects(session.id, "owner", start=page.next_start, limit=1)
    assert [e.invocation_id for e in tail.items] == ["2"]
    assert tail.next_start is None
    assert (await store.snapshot(session.id, "owner")).view.summary.unresolved_effect_count == 2
    with pytest.raises(UnknownSession):
        await store.effects(session.id, "other")


async def test_feed_replay_is_owner_scoped_and_filters_without_sequence_gaps(store):
    one = await store.create("owner", SessionInit())
    before = await store.snapshot(one.id, "owner")
    await store.create("owner", SessionInit())
    await store.create("other", SessionInit())
    await store.update(one.id, "owner", SessionPatch(title="Later"))
    feed = store.changes("owner", session_id=one.id, since=before.seq)
    try:
        change = await asyncio.wait_for(anext(feed), 2)
        assert change.seq == before.seq + 2
        assert change.summary.title == "Later"
        assert change.session_id == one.id
        assert change.entries[0].kind == "session"
    finally:
        await feed.aclose()


async def test_feed_retention_never_silently_skips_commits():
    files = MemoryFiles(StorageLimits(feed_retention=2))
    store = await JsonlSessionStore.open(files)
    for _ in range(3):
        await store.create("owner", SessionInit())
    feed = store.changes("owner", since=0)
    with pytest.raises(ChangeGap, match="too_old"):
        await anext(feed)
    await feed.aclose()


async def test_updates_rebuild_exact_summary_settings_and_feed(store):
    session = await store.create("owner", SessionInit())
    await store.update(
        session.id,
        "owner",
        SessionPatch(title="Fabricated", tags=("selected",), settings={"approval": "manual"}),
    )
    expected = await store.snapshot(session.id, "owner")
    files = store.files
    if isinstance(files, JsonlRoot):
        assert await files.aclose()
        reopened = await JsonlRoot.open(files.path)
    else:
        reopened = MemoryFiles()
        reopened.records = {
            key: [r.model_copy(deep=True) for r in values] for key, values in files.records.items()
        }
    try:
        rebuilt = await JsonlSessionStore.open(reopened)
        assert await rebuilt.snapshot(session.id, "owner") == expected
        feed = rebuilt.changes("owner", since=expected.seq - 1)
        change = await anext(feed)
        assert change.summary == expected.view.summary
        await feed.aclose()
    finally:
        if isinstance(reopened, JsonlRoot):
            assert await reopened.aclose()


async def test_patch_omission_and_null_have_explicit_meaning(store):
    session = await store.create("owner", SessionInit(title="Fabricated", tags=("keep",)))
    await store.update(session.id, "owner", SessionPatch(tags=None, settings={"x.fabricated": 1}))
    view = await store.get(session.id, "owner")
    assert view.summary.title == "Fabricated"
    assert view.summary.tags == ("keep",)
    cleared = await store.update(session.id, "owner", SessionPatch(title=None))
    assert cleared.title is None
    assert cleared.tags == ("keep",)


async def test_list_get_and_find_do_not_read_transcript_entries(store):
    session = await store.create("owner", SessionInit(ref="fabricated"))

    class UnreadableEntries(list):
        def __iter__(self):
            raise AssertionError("metadata path read transcript")

        def __len__(self):
            raise AssertionError("metadata path read transcript")

        def __getitem__(self, key):
            raise AssertionError("metadata path read transcript")

    store.index[session.id].entries = UnreadableEntries()
    assert (await store.list(SessionQuery(owner="owner"))).items[0].id == session.id
    assert (await store.get(session.id, "owner")).summary.id == session.id
    assert (await store.find("owner", "fabricated")).id == session.id
