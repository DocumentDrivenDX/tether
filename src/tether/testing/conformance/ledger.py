"""Indexed pending/effect metadata and persisted feed checks, with fabricated immutable refs."""

import asyncio

from tether.types.canonical import digest
from tether.types.execution import (
    ApprovalBinding,
    CallManifest,
    EntryConflict,
    HistoryRef,
    NewEntry,
)
from tether.types.storage import SessionInit, SessionStore

from .types import ConformanceFailure, require


async def pending_effects(store: SessionStore, owner: str, ref: str) -> None:
    sid = (await store.create(owner, SessionInit(ref=ref))).id
    source = HistoryRef(
        provider="fabricated",
        key="source",
        sha256="fabricated",
        codec="tether-history-v1",
        owner=owner,
        session_id=sid,
    )
    manifest = CallManifest(
        position=0,
        call_id="call",
        tool="fabricated",
        args_digest="args",
        tool_digest="tool",
        effect="effectful",
        replay="unsafe",
    )
    inv = digest([sid, 1, 0])
    binding = ApprovalBinding(
        session_id=sid,
        invocation_id=inv,
        actor_id=owner,
        args_digest="args",
        tool_digest="tool",
        spec_digest="spec",
    )
    async with store.turn(sid, owner, turn_id="ledger") as log:
        await log.append(
            [
                NewEntry(kind="turn_started", turn_id="phase"),
                NewEntry(kind="message", history_ref=source, call_manifest=(manifest,)),
                NewEntry(
                    kind="confirmation",
                    facts={"state": "requested", "binding": binding.model_dump(mode="json")},
                ),
            ]
        )
        snapshot = await store.snapshot(sid, owner)
        pending = snapshot.view.pending
        require(
            pending is not None
            and pending.invocation_id == inv
            and pending.history_ref == source
            and pending.arguments is None
            and pending.history_status == "not_loaded",
            "pending_source_metadata",
        )
        require(snapshot.view.summary.pending, "pending_projection")
        await log.append(
            [
                NewEntry(
                    kind="confirmation",
                    facts={"state": "approved", "binding": binding.model_dump(mode="json")},
                ),
                NewEntry(
                    kind="tool_call",
                    facts={
                        "invocation_id": inv,
                        "tool": "fabricated",
                        "effect": "effectful",
                        "status": "started",
                    },
                ),
            ]
        )
        snapshot = await store.snapshot(sid, owner)
        require(
            not snapshot.view.summary.pending and snapshot.view.pending is None,
            "pending_decision_projection",
        )
        require(snapshot.view.summary.unresolved_effect_count == 1, "unknown_effect_count")
        effects = await store.effects(sid, owner, limit=1)
        require(
            len(effects.items) == 1
            and effects.items[0].invocation_id == inv
            and effects.items[0].intent_index == 4,
            "indexed_effects",
        )
        resolved = NewEntry(
            kind="resolved", key="resolution", facts={"invocation_id": inv, "outcome": "completed"}
        )
        accepted = await log.append([resolved])
        snapshot = await store.snapshot(sid, owner)
        require(snapshot.view.summary.unresolved_effect_count == 0, "resolved_projection")
        require(not (await store.effects(sid, owner)).items, "resolved_effect_index")
        require(await log.append([resolved]) == accepted, "resolution_idempotency")
        try:
            await log.append(
                [
                    resolved.model_copy(
                        update={"facts": {"invocation_id": inv, "outcome": "not_completed"}}
                    )
                ]
            )
        except EntryConflict:
            pass
        else:
            raise ConformanceFailure("resolution_key_conflict")
        require(await store.snapshot(sid, owner) == snapshot, "resolution_conflict_atomicity")
        await log.append([NewEntry(kind="turn_ended", facts={"stop_reason": "final"})])


async def snapshot_feed(store: SessionStore, owner: str, ref: str) -> None:
    sid = (await store.create(owner, SessionInit(ref=ref))).id
    watermark = await store.watermark(owner)
    feed = store.changes(owner, session_id=sid, since=watermark)
    try:
        async with store.turn(sid, owner, turn_id="feed") as log:
            values = [
                NewEntry(kind="turn_started", key="feed-start", turn_id="feed"),
                NewEntry(kind="message", key="feed-message", turn_id="feed"),
            ]
            task = asyncio.create_task(log.append(values))
            try:
                snapshot = await store.snapshot(sid, owner)
            finally:
                accepted = await task
            require(snapshot.through_index in {-1, 1}, "snapshot_atomic_prefix")
            require(
                snapshot.view.summary.entry_count == snapshot.through_index + 1,
                "snapshot_atomic_projection",
            )
            change = await anext(feed)
            require(
                change.seq == watermark + 1 and change.entries == accepted,
                "persisted_feed_atomic_batch",
            )
            require(
                change.summary is not None and change.summary.entry_count == 2,
                "persisted_feed_projection",
            )
            require(await log.append(values) == accepted, "feed_retry_identity")
            require(await store.watermark(owner) == change.seq, "feed_retry_no_sequence")
            await log.append(
                [NewEntry(kind="turn_ended", turn_id="feed", facts={"stop_reason": "final"})]
            )
            last = await anext(feed)
            require(
                last.seq == change.seq + 1 and len(last.entries) == 1, "persisted_feed_continuation"
            )
        resumed = store.changes(owner, session_id=sid, since=change.seq)
        try:
            require(await anext(resumed) == last, "persisted_feed_reconnect")
        finally:
            await resumed.aclose()
    finally:
        await feed.aclose()
