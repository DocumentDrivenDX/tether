"""Shared logical SessionStore checks; adapters own resource and fault qualification."""

import asyncio
from uuid import uuid4

from tether.types.execution import EntryConflict, ExecutionDenied, NewEntry
from tether.types.storage import (
    SessionInit,
    SessionPatch,
    SessionQuery,
    SessionStore,
    TurnInProgress,
)

from .ledger import pending_effects, snapshot_feed
from .mutations import REQUIRED_EXTERNAL, logical_mutation_receipts
from .types import ConformanceFailure, LiveStoreFactory, LiveStoreFixture, SuiteReport, require


async def caller_scope(h: LiveStoreFixture) -> None:
    store = h.store.for_actor(h.actor)
    try:
        await store.create(h.foreign.id, SessionInit())
    except ExecutionDenied:
        return
    raise ConformanceFailure("caller_owner_mismatch")


async def journal(store: SessionStore, owner: str, sid: str) -> None:
    start = NewEntry(kind="turn_started", turn_id="original", key="start")
    message = NewEntry(kind="message", turn_id="original", key="message")
    async with store.turn(sid, owner, turn_id="original") as log:
        first = await log.append([start, message])
        require(tuple(e.index for e in first) == (0, 1), "dense_positions")
        snapshot = await store.snapshot(sid, owner)
        require(snapshot.through_index == 1, "snapshot_prefix")
        require(snapshot.view.summary.status == "running", "running_projection")
        require(snapshot.view.summary.entry_count == 2, "entry_projection")
        require(snapshot.view.summary.message_count == 1, "message_projection")
        require(await log.append([start, message]) == first, "stable_key_retry")
        require(await store.snapshot(sid, owner) == snapshot, "retry_no_publication")
        try:
            await log.append([NewEntry(kind="message"), start.model_copy(update={"kind": "other"})])
        except EntryConflict:
            pass
        else:
            raise ConformanceFailure("conflicting_key_refused")
        require(await store.snapshot(sid, owner) == snapshot, "rejected_batch_atomicity")
        try:
            async with store.turn(sid, owner, turn_id="competing"):
                raise ConformanceFailure("exclusive_turn")
        except TurnInProgress:
            pass
        await log.heartbeat()
        await log.append(
            [NewEntry(kind="turn_ended", turn_id="original", facts={"stop_reason": "final"})]
        )
    snapshot = await store.snapshot(sid, owner)
    require(snapshot.through_index == 2, "terminal_snapshot_prefix")
    require(snapshot.view.summary.entry_count == 3, "terminal_entry_projection")
    require(snapshot.view.summary.status == "idle", "terminal_projection")
    require(snapshot.view.summary.turn_count == 1, "turn_projection")
    page = await store.entries(sid, owner, start=0, limit=2)
    require(tuple(e.index for e in page.items) == (0, 1), "entry_page")
    require(page.next_start == 2, "entry_continuation")
    reverse = await store.entries(sid, owner, start=2, limit=2, reverse=True)
    require(tuple(e.index for e in reverse.items) == (2, 1), "reverse_page")
    require(
        (await store.lookup(sid, owner, kind="key", value="message")) == (first[1],), "key_lookup"
    )
    snapshot.view.settings["fabricated_mutation"] = True
    require(
        "fabricated_mutation" not in (await store.snapshot(sid, owner)).view.settings,
        "copied_projection",
    )


async def metadata(h: LiveStoreFixture, ref: str) -> str:
    actor, foreign = h.actor, h.foreign
    require(actor.id != foreign.id, "distinct_fixture_actors")
    store, other = h.store.for_actor(actor), h.store.for_actor(foreign)
    session = await store.create(actor.id, SessionInit(ref=ref, title="Fabricated"))
    require(await store.create(actor.id, SessionInit(ref=ref)) == session, "idempotent_create")
    require(
        (await other.create(foreign.id, SessionInit(ref=ref))).id != session.id,
        "owner_ref_namespace",
    )
    require(await other.get(session.id, foreign.id) is None, "owner_read_isolation")
    found = await store.find(actor.id, ref)
    require(found is not None and found.id == session.id, "reference_lookup")
    updated = await store.update(session.id, actor.id, SessionPatch(tags=("fabricated",)))
    require(updated.title == "Fabricated" and updated.tags == ("fabricated",), "metadata_patch")
    cleared = await store.update(session.id, actor.id, SessionPatch(title=None))
    require(cleared.title is None, "explicit_title_clear")
    listed = await store.list(SessionQuery(owner=actor.id, ref=ref), limit=1)
    require(tuple(s.id for s in listed.items) == (session.id,), "indexed_metadata_query")
    return session.id


async def live_store_suite(
    factory: LiveStoreFactory, *, timeout_seconds: float = 30
) -> SuiteReport:
    """Await in a test; factory owns isolated resources and reopens the same durable namespace."""
    if not 0 < timeout_seconds < float("inf"):
        raise ValueError("conformance_timeout_invalid")
    ref = "fabricated-conformance-" + uuid4().hex
    async with asyncio.timeout(timeout_seconds):
        async with factory() as h:
            capabilities = h.store.capabilities.model_copy(deep=True)
            await caller_scope(h)
            if capabilities.mutation_receipts == 1:
                await logical_mutation_receipts(h)
            await metadata(h, ref)
            store = h.store.for_actor(h.actor)
            sid = (await store.create(h.actor.id, SessionInit(ref=ref + "-journal"))).id
            await journal(store, h.actor.id, sid)
            await pending_effects(store, h.actor.id, ref + "-ledger")
            await snapshot_feed(store, h.actor.id, ref + "-feed")
            snapshot = await store.snapshot(sid, h.actor.id)
            durable = capabilities.durability != "none"
        if durable:
            async with factory() as reopened:
                require(
                    await reopened.store.for_actor(reopened.actor).snapshot(sid, reopened.actor.id)
                    == snapshot,
                    "durable_reopen",
                )
    checks = (
        "caller_scope",
        "owner_isolation",
        "reference_idempotency",
        "metadata_patch",
        "journal_atomicity",
        "dense_positions",
        "logical_projections",
        "key_lookup",
        "entry_pages",
        "exclusive_turn",
        "copied_projection",
        "pending_metadata",
        "effects_resolution",
        "logical_snapshot_feed",
    )
    return SuiteReport(
        suite="live_store",
        capabilities=capabilities.model_dump(mode="json"),
        checks=checks
        + (("durable_reopen",) if durable else ())
        + (("mutation_receipts",) if capabilities.mutation_receipts else ()),
        required_external=(
            "projection_rebuild_equivalence",
            "contended_snapshot_approval_writes",
            "lease_expiry_stale_recovery",
            "keyset_stability_no_transcript_scan",
            "context_record_closure",
            "unknown_acknowledgment",
            "pending_source_bounds",
            "retention_backpressure",
            "worker_shutdown",
            "adapter_faults",
        )
        + (REQUIRED_EXTERNAL if capabilities.mutation_receipts else ())
        + (("restart_faults",) if durable else ())
        + (("cross_process_fencing",) if capabilities.lease == "cross_process" else ()),
    )
