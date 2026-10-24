"""Bounded pending projections and exact journal restore before public store exposure."""

from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_postgres_primitives import append, contents

from tether.stores.postgres import DatabaseRoot, migrate
from tether.stores.postgres.journal import Journal
from tether.stores.projections import create_index
from tether.types.canonical import digest
from tether.types.execution import (
    Actor,
    ApprovalBinding,
    CallManifest,
    CommitUnknown,
    EntryConflict,
    ExecutionDenied,
    HistoryRef,
    NewEntry,
    SessionState,
)
from tether.types.storage import SessionArchive, SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def pending_values(summary, actor):
    manifests = tuple(
        CallManifest(
            position=i,
            call_id=f"call-{i}",
            tool="fabricated-tool",
            args_digest="args",
            tool_digest="tool",
            effect="effectful",
            replay="unsafe",
        )
        for i in range(2)
    )
    source = NewEntry(
        kind="message",
        key="proposal",
        turn_id="turn",
        call_manifest=manifests,
        history_ref=HistoryRef(
            provider="fabricated",
            key="source",
            sha256="hash",
            owner=actor.id,
            session_id=summary.id,
        ),
    )
    invocations = [digest([summary.id, 1, i]) for i in range(2)]
    requests = [
        NewEntry(
            kind="confirmation",
            key="ask-" + str(i),
            turn_id="turn",
            facts={
                "state": "requested",
                "binding": ApprovalBinding(
                    session_id=summary.id,
                    invocation_id=inv,
                    args_digest="args",
                    tool_digest="tool",
                    spec_digest="spec",
                    actor_id=actor.id,
                ).model_dump(mode="json"),
            },
        )
        for i, inv in enumerate(invocations)
    ]
    values = [
        NewEntry(
            kind="turn_started",
            turn_id="turn",
            facts={"spec_id": "demo", "spec_version": "1", "spec_digest": "spec"},
        ),
        source,
        *requests,
        requests[0].model_copy(update={"key": "ask-again"}),
        NewEntry(
            kind="tool_call",
            key="intent-zero",
            facts={
                "invocation_id": invocations[0],
                "tool": "fabricated-tool",
                "effect": "effectful",
                "status": "started",
            },
        ),
        NewEntry(kind="tool_call", facts={"invocation_id": invocations[0], "status": "unknown"}),
        NewEntry(
            kind="tool_call",
            key="intent-one",
            facts={
                "invocation_id": invocations[1],
                "tool": "fabricated-tool",
                "effect": "effectful",
                "status": "started",
            },
        ),
        NewEntry(kind="resolved", facts={"invocation_id": invocations[0], "outcome": "completed"}),
        NewEntry(kind="turn_ended", turn_id="turn", facts={"stop_reason": "confirmation_required"}),
    ]
    return values, invocations


async def view(journal, session_id):
    async def read(connection):
        await journal.head(connection, write=False)
        frame = await journal.frame(connection, session_id, write=False)
        return await journal.view(connection, frame)

    return await journal.root.run(journal.actor, session_id, read)


async def test_pending_replacement_keeps_original_order_and_effect_indexes_match_rebuild(database):
    h = database
    journal = Journal(h.root, h.actor)
    initial = SessionInit(settings={"approval": "manual"})
    summary = await journal.create(initial, str(uuid4()))
    values, invocations = pending_values(summary, h.actor)
    accepted, frame = await append(journal, summary.id, values)
    oracle = create_index(summary.id, h.actor.id, initial, summary.created_at)
    for entry in accepted:
        oracle.apply(entry)
    oracle.summary = oracle.summary.model_copy(
        update={"last_seq": frame.projection.summary.last_seq}
    )
    current = await view(journal, summary.id)
    assert current == oracle.view()
    assert current.pending.invocation_id == invocations[0]
    assert current.pending.arguments is None
    assert current.summary.unresolved_effect_count == 1
    decision = values[2].model_copy(
        update={"key": "answered", "facts": {**values[2].facts, "state": "approved"}}
    )
    await append(journal, summary.id, [decision])
    assert (await view(journal, summary.id)).pending.invocation_id == invocations[1]


async def test_reference_preserving_restore_is_exact_and_idempotent_without_execution(database):
    h = database
    original = Journal(h.root, h.actor)
    init = SessionInit(ref="fabricated-ref", settings={"approval": "manual"})
    summary = await original.create(init, str(uuid4()))
    values, _ = pending_values(summary, h.actor)
    accepted, frame = await append(original, summary.id, values)
    archive = SessionArchive(
        state=frame.projection.state, init=frame.projection.init, entries=accepted
    )
    schema = "restore_" + uuid4().hex
    await migrate(h.source, actor=h.actor, schema=schema)
    root = await DatabaseRoot.open(h.source, actor=h.actor, schema=schema)
    target = Journal(root, h.actor)
    try:
        restored = await target.restore(archive, created_at=summary.created_at)
        assert restored.pending
        assert restored.unresolved_effect_count == 1
        assert await target.restore(archive, created_at=summary.created_at) == restored
        assert (await contents(target, summary.id))[2] == accepted
        assert (await contents(target, summary.id))[0] == (1, 0)
        with pytest.raises(EntryConflict, match="archive_restore_conflict"):
            await target.restore(archive, created_at=summary.created_at + timedelta(seconds=1))
        foreign = Journal(root, Actor(id="foreign"))
        with pytest.raises(ExecutionDenied, match="denied"):
            await foreign.restore(archive, created_at=summary.created_at)
    finally:
        assert await root.aclose()
        async with h.pool.connection() as connection:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


async def test_unknown_restore_ack_reopens_and_finds_exact_original_commit(database):
    h = database
    sid = str(uuid4())
    # Construct an empty authoritative execution archive with a real aware timestamp.
    original = Journal(h.root, h.actor)
    at = await h.root.run(h.actor, None, original.clock)

    archive = SessionArchive(
        state=SessionState(id=sid, owner=h.actor.id), init=SessionInit(), entries=()
    )
    fail = False

    @asynccontextmanager
    async def acquisition(actor, session_id):
        async with h.pool.connection() as connection:
            yield connection
        if fail:
            raise RuntimeError("fabricated_restore_ack_loss")

    root = await DatabaseRoot.open(acquisition, actor=h.actor, schema=h.schema)
    target = Journal(root, h.actor)
    fail = True
    try:
        with pytest.raises(CommitUnknown, match="database_release_unknown"):
            await target.restore(archive, created_at=at)
    finally:
        assert await root.aclose()
    replacement = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    try:
        target = Journal(replacement, h.actor)
        restored = await target.restore(archive, created_at=at)
        assert restored.entry_count == 0
        assert (await contents(target, sid))[0] == (1, 0)
    finally:
        assert await replacement.aclose()
