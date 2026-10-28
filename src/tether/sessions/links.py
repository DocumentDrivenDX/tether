"""Accepted, bounded history replacement selection and link admission."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from tether.types.canonical import canonical, digest
from tether.types.client import ReadLimit
from tether.types.execution import Actor, Entry, HistoryRef, NewEntry, TurnLog, copy_actor
from tether.types.history import HistoryLink, HistoryValue
from tether.types.history_links import select
from tether.types.storage import IntegrityError

from .access import SessionAccess


async def selected(
    access: SessionAccess,
    actor: Actor,
    session_id: str,
    message_index: int,
    original: HistoryRef,
    *,
    through_index: int,
) -> HistoryRef:
    entries = await access.store_for(actor).lookup(
        session_id,
        actor.id,
        kind="history_link",
        value=str(message_index),
        limit=access.limits.max_lookup_entries,
        max_bytes=access.limits.max_read_bytes,
    )
    return select(
        original,
        message_index,
        entries,
        through_index=through_index,
        max_hops=access.limits.max_history_link_hops,
    )


async def hydrate(
    access: SessionAccess,
    actor: Actor,
    session_id: str,
    message_index: int,
    original: HistoryRef,
    *,
    through_index: int,
) -> HistoryValue:
    try:
        ref = await selected(
            access, actor, session_id, message_index, original, through_index=through_index
        )
    except IntegrityError:
        return HistoryValue(ref=original, status="integrity_error", reason="history_link_integrity")
    return await access.hydrate(actor, session_id, ref)


def _source_ref(log: TurnLog, link: HistoryLink, actor: Actor, session_id: str) -> HistoryRef:
    if link.message_index >= len(log.entries):
        raise IntegrityError("history_link_source")
    source = log.entries[link.message_index]
    raw = source.history_ref or source.facts.get("history_ref")
    try:
        original = HistoryRef.model_validate(raw)
    except ValueError:
        raise IntegrityError("history_link_source") from None
    if (
        original.owner != actor.id
        or original.session_id != session_id
        or original.sha256 != link.canonical_digest
        or original.kind != link.previous.kind
        or original.codec != link.previous.codec
        or original.component != link.previous.component
    ):
        raise IntegrityError("history_link_source")
    return original


def _equivalent_values(before: HistoryValue, after: HistoryValue, link: HistoryLink) -> bool:
    return (
        before.status == "ready"
        and after.status == "ready"
        and before.record is not None
        and after.record is not None
        and before.record == after.record
        and digest(before.record.model_dump(mode="json")) == link.canonical_digest
    )


async def _commit_link(
    access: SessionAccess,
    actor: Actor,
    session_id: str,
    link: HistoryLink,
    entry_key: str,
) -> Entry:
    await access.authorize(actor, session_id, "link_history", session_id=session_id)
    async with access.store_for(actor).turn(
        session_id, actor.id, turn_id="history_link:" + str(uuid4())
    ) as log:
        original = _source_ref(log, link, actor, session_id)
        old = await access.store_for(actor).lookup(
            session_id, actor.id, kind="key", value=entry_key, limit=1
        )
        if old:
            if old[0].kind != "history_linked" or old[0].facts != link.model_dump(mode="json"):
                raise IntegrityError("history_link_key_conflict")
            return old[0]
        prior = await access.store_for(actor).lookup(
            session_id,
            actor.id,
            kind="history_link",
            value=str(link.message_index),
            limit=access.limits.max_lookup_entries,
            max_bytes=access.limits.max_read_bytes,
        )
        reachable = {canonical(original.model_dump(mode="json"))}
        for entry in prior:
            if entry.index < len(log.entries):
                try:
                    accepted_link = HistoryLink.model_validate(entry.facts)
                except ValueError:
                    raise IntegrityError("history_link_integrity") from None
                reachable.add(canonical(accepted_link.verified.model_dump(mode="json")))
        if canonical(link.previous.model_dump(mode="json")) not in reachable:
            raise IntegrityError("history_link_unreachable")
        proposed = Entry(
            kind="history_linked",
            index=len(log.entries),
            at=datetime.now(UTC),
            facts=link.model_dump(mode="json"),
        )
        select(
            original,
            link.message_index,
            (*prior, proposed),
            through_index=proposed.index,
            max_hops=access.limits.max_history_link_hops,
        )
        before = await access.hydrate(actor, session_id, link.previous)
        after = await access.hydrate(actor, session_id, link.verified)
        if not _equivalent_values(before, after, link):
            raise IntegrityError("history_link_equivalence")
        await access.authorize(actor, session_id, "link_history", session_id=session_id)
        accepted = await log.append(
            (NewEntry(kind="history_linked", key=entry_key, facts=link.model_dump(mode="json")),)
        )
        return accepted[0]


async def bind(
    access: SessionAccess,
    actor: Actor,
    session_id: str,
    message_index: int,
    previous: HistoryRef,
    verified: HistoryRef,
    *,
    key: str,
) -> Entry:
    """Verify equivalent ready content, then append one lease-fenced link fact."""

    actor = copy_actor(actor)
    access.check()
    if type(message_index) is not int or message_index < 0:
        raise ReadLimit("read_position")
    if not key or len(key.encode()) > access.limits.max_request_bytes:
        raise ReadLimit("request_bytes")
    link = HistoryLink(
        message_index=message_index,
        previous=previous.model_copy(deep=True),
        verified=verified.model_copy(deep=True),
        canonical_digest=previous.sha256,
    )
    if previous == verified:
        raise IntegrityError("history_link_cycle")
    if (
        previous.owner != actor.id
        or previous.session_id != session_id
        or len(canonical(link.model_dump(mode="json"))) > access.limits.max_request_bytes
    ):
        raise ReadLimit("request_bytes")
    return await access.metadata(
        actor,
        session_id,
        lambda: _commit_link(access, actor, session_id, link, "history_link:" + key),
        session_id=session_id,
    )
