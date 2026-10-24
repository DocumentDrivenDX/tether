"""Public archive port checks using a self-contained fabricated immutable bundle."""

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime
from uuid import uuid4

from tether.types import Message
from tether.types.archive import ArchivedSession, ArchiveReadError, NotRestorable
from tether.types.canonical import digest
from tether.types.execution import (
    Entry,
    EntryConflict,
    ExecutionDenied,
    HistoryRef,
    SessionState,
    copy_actor,
)
from tether.types.history import HistoryRecord, StoredHistory
from tether.types.storage import (
    ArchiveBundle,
    SessionArchive,
    SessionInit,
    SessionQuery,
    SessionSummary,
    SessionView,
    UnknownSession,
    Unsupported,
)

from .types import ArchiveFactory, ArchiveFixture, ConformanceFailure, SuiteReport, require


def sample_archive() -> ArchivedSession:
    """Fresh fabricated source closure; no application transcript or private provider is read."""
    sid, owner = uuid4().hex, "fabricated-conformance-owner"
    at = datetime(2026, 1, 1, tzinfo=UTC)
    record = HistoryRecord(kind="message", message=Message(role="user", content="Fabricated."))
    ref = HistoryRef(
        provider="fabricated",
        codec="tether-history-v1",
        key="source",
        owner=owner,
        session_id=sid,
        sha256=digest(record.model_dump(mode="json")),
    )
    entries = (Entry(kind="message", index=0, at=at, key="original", history_ref=ref),)
    init = SessionInit(ref="fabricated-archive-" + sid)
    bundle = ArchiveBundle(
        session=SessionArchive(state=SessionState(id=sid, owner=owner), init=init, entries=entries),
        created_at=at,
        records=(StoredHistory(ref=ref, record=record).model_dump(mode="json"),),
        sha256="",
    )
    bundle = bundle.model_copy(
        update={"sha256": digest(bundle.model_dump(mode="json", exclude={"sha256"}))}
    )
    return ArchivedSession(
        bundle=bundle,
        view=SessionView(
            summary=SessionSummary(
                id=sid,
                owner=owner,
                ref=init.ref,
                tier="archive",
                created_at=at,
                updated_at=at,
                entry_count=1,
                message_count=1,
            )
        ),
    )


async def forbidden(operation: Awaitable[object], check: str) -> None:
    try:
        await operation
    except (UnknownSession, ExecutionDenied):
        return
    except ArchiveReadError as error:
        require(error.status == "denied", check)
        return
    raise ConformanceFailure(check)


async def conflict(h: ArchiveFixture, sample: ArchivedSession) -> None:
    if h.sink is None:
        raise ConformanceFailure("archive_writable_fixture")
    entries = sample.bundle.session.entries
    changed = entries[0].model_copy(update={"facts": {"fabricated_change": True}})
    bundle = sample.bundle.model_copy(
        update={
            "session": sample.bundle.session.model_copy(update={"entries": (changed, *entries[1:])})
        }
    )
    bundle = bundle.model_copy(
        update={"sha256": digest(bundle.model_dump(mode="json", exclude={"sha256"}))}
    )
    try:
        candidate = sample.model_copy(update={"bundle": bundle})
        await h.sink.archive(candidate.model_copy(deep=True))
    except EntryConflict:
        return
    raise ConformanceFailure("archive_conflicting_snapshot")


async def reads(h: ArchiveFixture, sample: ArchivedSession) -> None:
    source = h.source
    actor, foreign = copy_actor(h.actor), copy_actor(h.foreign)
    require(
        actor.id == sample.view.summary.owner and foreign.id != actor.id, "archive_fixture_actors"
    )
    sid = sample.view.summary.id
    view = await source.get(sid, actor=copy_actor(actor))
    require(view is not None, "archive_present")
    require(await source.get(sid, actor=copy_actor(foreign)) is None, "archive_owner_isolation")
    listed = await source.list(
        SessionQuery(owner=actor.id), cursor=None, limit=10, actor=copy_actor(actor)
    )
    require(any(s.id == sid for s in listed.items), "archive_list")
    require(
        not (
            await source.list(
                SessionQuery(owner=foreign.id), cursor=None, limit=10, actor=copy_actor(foreign)
            )
        ).items,
        "archive_list_owner",
    )
    await forbidden(
        source.entries(sid, actor=copy_actor(foreign), start=0, limit=10, reverse=False),
        "archive_entries_owner",
    )
    page = await source.entries(sid, actor=copy_actor(actor), start=0, limit=10, reverse=False)
    if source.capabilities.kind == "derived":
        require(view is not None and view.summary.partial and page.partial, "derived_partial")
        empty = await source.entries(
            sid,
            actor=copy_actor(actor),
            start=len(sample.bundle.session.entries),
            limit=10,
            reverse=False,
        )
        require(not empty.items and empty.partial, "derived_empty_partial")
    else:
        require(view == sample.view, "archive_view_lossless")
        require(
            page.items == sample.bundle.session.entries and not page.partial,
            "archive_entries_lossless",
        )
    refs = tuple(e.history_ref for e in sample.bundle.session.entries if e.history_ref is not None)
    values = await source.read(refs, actor=copy_actor(actor))
    require(tuple(v.ref for v in values) == refs, "archive_read_order")
    if source.capabilities.content == "full" and source.capabilities.lossless:
        require(
            all(v.status == "ready" and v.record is not None for v in values),
            "archive_content_lossless",
        )
        require(
            tuple(v.record for v in values)
            == tuple(StoredHistory.model_validate(raw).record for raw in sample.bundle.records),
            "archive_original_records",
        )
    foreign_values = await source.read(refs, actor=copy_actor(foreign))
    require(
        len(foreign_values) == len(refs) and all(v.status == "denied" for v in foreign_values),
        "archive_content_owner",
    )
    if source.capabilities.restorable:
        await forbidden(source.restore(sid, actor=copy_actor(foreign)), "archive_restore_owner")
        require(
            await source.restore(sid, actor=copy_actor(actor)) == sample, "archive_restore_lossless"
        )
    else:
        try:
            await source.restore(sid, actor=copy_actor(actor))
        except (NotRestorable, Unsupported):
            pass
        else:
            raise ConformanceFailure("archive_restore_capability")


async def archive_suite(factory: ArchiveFactory, *, timeout_seconds: float = 30) -> SuiteReport:
    """Factories preload source-only fixtures; supplied sinks are qualified independently."""
    if not 0 < timeout_seconds < float("inf"):
        raise ValueError("conformance_timeout_invalid")
    sample = sample_archive()
    async with asyncio.timeout(timeout_seconds):
        async with factory(sample.model_copy(deep=True)) as h:
            require(
                h.actor.id == sample.view.summary.owner and h.foreign.id != h.actor.id,
                "archive_fixture_actors",
            )
            capabilities = h.source.capabilities.model_copy(deep=True)
            sink_capabilities = (
                h.sink.capabilities.model_copy(deep=True) if h.sink is not None else None
            )
            if h.sink is not None:
                require(
                    sink_capabilities is not None and sink_capabilities.writable,
                    "archive_sink_writable",
                )
                await h.sink.archive(sample.model_copy(deep=True))
                await h.sink.archive(sample.model_copy(deep=True))
                if sink_capabilities is not None and sink_capabilities.kind == "authoritative":
                    await conflict(h, sample)
            await reads(h, sample)
    return SuiteReport(
        suite="archive",
        capabilities=capabilities.model_dump(mode="json"),
        sink_capabilities=sink_capabilities.model_dump(mode="json")
        if sink_capabilities is not None
        else None,
        checks=(("archive_idempotency",) if sink_capabilities is not None else ())
        + (
            "archive_owner_isolation",
            "archive_metadata",
            "archive_content",
            "archive_restore_capability",
        ),
        required_external=("readiness_pending_denied_unavailable", "bounded_transfer")
        + (("live_restore_integration",) if capabilities.restorable else ()),
    )
