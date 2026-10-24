"""Optional receipt companion logical checks, with explicit external qualification gaps."""

import asyncio
from uuid import uuid4

from tether.types.execution import ExecutionDenied
from tether.types.mutations import MutationReceipts, receipt_store
from tether.types.storage import SessionInit, SessionPatch, SessionStore, Unsupported

from .types import LiveStoreFactory, LiveStoreFixture, SuiteReport, require

REQUIRED_EXTERNAL = (
    "mutation_receipt_unknown_ack",
    "mutation_receipt_concurrent_updates",
    "mutation_receipt_credentials",
    "mutation_receipt_bounds",
)


async def creation(port: MutationReceipts, owner: str, ref: str) -> str:
    receipts = await asyncio.gather(
        *[
            port.create_with_receipt(owner, SessionInit(ref=ref, title="Fabricated"))
            for _ in range(3)
        ]
    )
    require(sum(r.disposition == "created" for r in receipts) == 1, "one_genesis")
    sid = receipts[0].summary.id
    require(all(r.summary.id == sid for r in receipts), "same_reference_identity")
    made = next(r for r in receipts if r.disposition == "created")
    require(made.commit is not None and made.commit.seq == made.summary.last_seq, "exact_genesis")
    return sid


async def patches(
    store: SessionStore, port: MutationReceipts, owner: str, sid: str, ref: str
) -> None:
    first = await port.update_with_receipt(
        owner=owner,
        session_id=sid,
        patch=SessionPatch(title="Updated", tags=("fabricated",), settings={"fabricated": True}),
    )
    require(first.disposition == "accepted" and first.commit is not None, "accepted_metadata")
    require(first.commit is not None and len(first.commit.entries) == 3, "bounded_metadata_entries")
    require(first.summary.owner == owner and first.summary.id == sid, "receipt_scope")
    if first.commit is not None:
        require(
            (first.commit.owner, first.commit.session_id, first.commit.seq)
            == (owner, sid, first.summary.last_seq),
            "receipt_commit_scope",
        )
        page = await store.entries(sid, owner, start=first.commit.entries[0].index, limit=3)
        require(page.items == first.commit.entries, "exact_accepted_metadata")
    again = await port.create_with_receipt(owner, SessionInit(ref=ref))
    require(again.disposition == "existing" and again.commit is None, "existing_after_update")
    require(again.summary == first.summary, "existing_current_summary")
    empty = await port.update_with_receipt(sid, owner, SessionPatch())
    require(empty.disposition == "noop" and empty.commit is None, "empty_patch_noop")
    require(empty.summary == first.summary, "noop_frontier")
    same = await port.update_with_receipt(sid, owner, SessionPatch(title="Updated"))
    require(same.disposition == "accepted" and same.commit is not None, "equal_value_is_accepted")
    require(same.summary.last_seq > first.summary.last_seq, "exact_update_sequence")
    cleared = await port.update_with_receipt(sid, owner, SessionPatch(title=None))
    require(cleared.summary.title is None, "explicit_title_clear_receipt")
    if cleared.commit is not None:
        cleared.commit.entries[0].facts["value"] = "fabricated_mutation"
    next_receipt = await port.update_with_receipt(sid, owner, SessionPatch())
    require(next_receipt.summary.title is None, "copied_mutation_receipt")


async def logical_mutation_receipts(h: LiveStoreFixture) -> None:
    store = h.store.for_actor(h.actor)
    port = receipt_store(store)
    if port is None:
        raise Unsupported("mutation_receipts_unavailable")
    ref = "fabricated-receipt-" + uuid4().hex
    sid = await creation(port, h.actor.id, ref)
    await patches(store, port, h.actor.id, sid, ref)
    stored = await store.entries(sid, h.actor.id, start=0, limit=10)
    require(stored.items[-1].facts.get("value") is None, "receipt_cannot_mutate_journal")
    try:
        await port.create_with_receipt(h.foreign.id, SessionInit())
    except ExecutionDenied:
        pass
    else:
        require(False, "receipt_caller_scope")


async def mutation_receipt_suite(
    factory: LiveStoreFactory, *, timeout_seconds: float = 30
) -> SuiteReport:
    if not 0 < timeout_seconds < float("inf"):
        raise ValueError("conformance_timeout_invalid")
    async with asyncio.timeout(timeout_seconds):
        async with factory() as h:
            capabilities = h.store.capabilities.model_copy(deep=True)
            await logical_mutation_receipts(h)
    return SuiteReport(
        suite="mutation_receipts",
        capabilities=capabilities.model_dump(mode="json"),
        checks=(
            "receipt_caller_scope",
            "one_genesis",
            "exact_genesis",
            "existing_after_update",
            "accepted_metadata",
            "empty_patch_noop",
            "equal_value_is_accepted",
            "exact_update_sequence",
            "exact_accepted_metadata",
            "receipt_scope",
            "receipt_commit_scope",
            "explicit_title_clear_receipt",
            "copied_mutation_receipt",
        ),
        required_external=REQUIRED_EXTERNAL,
    )
