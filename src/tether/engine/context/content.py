"""Bounded current reads of accepted summary content, separate from original messages."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, cast

from tether.types.canonical import digest
from tether.types.context import ContextError, ContextSummary
from tether.types.execution import CommitUnknown, EntryConflict, HistoryRef, copy_actor
from tether.types.history import HistoryRecord, HistoryValue
from tether.types.storage import StoreError

from ..governance import PolicyDenied
from .sources import charge

if TYPE_CHECKING:
    from .state import ContextState


def decoded(values: object, ref: HistoryRef) -> HistoryRecord:
    if not isinstance(values, tuple):
        raise ContextError("context_unavailable")
    items = cast(tuple[object, ...], values)
    if len(items) != 1:
        raise ContextError("context_unavailable")
    raw = items[0]
    if not isinstance(raw, HistoryValue) or set(raw.__dict__) != set(HistoryValue.model_fields):
        raise ContextError("context_unavailable")
    if raw.record is not None and set(raw.record.__dict__) != set(HistoryRecord.model_fields):
        raise ContextError("context_unavailable")
    value = HistoryValue.model_validate(raw.model_dump(mode="json"))
    if value.ref != ref:
        raise ContextError("context_incompatible")
    if value.status == "pending":
        raise ContextError("history_pending")
    if value.status != "ready" or value.record is None:
        raise ContextError("context_unavailable")
    return value.record


async def load(step: ContextState, summary: ContextSummary) -> str:
    history = step.services.history
    if history is None:
        raise ContextError("context_unconfigured")
    ref = summary.record.summary_ref
    if ref.owner != step.gov.actor.id or ref.session_id != step.gov.log.session.id:
        raise ContextError("context_incompatible")
    try:
        await step.gov.authorize("read_session", step.gov.log.session.id)
        try:
            values = await step.executor.workers.call(
                step.gov.log.session.id,
                partial(
                    history.read, (ref.model_copy(deep=True),), actor=copy_actor(step.gov.actor)
                ),
            )
        except (StoreError, CommitUnknown, EntryConflict):
            raise
        except Exception:
            raise ContextError("context_unavailable") from None
        record = decoded(values, ref)
        charge(record.model_dump(mode="json"), step.config.max_source_bytes)
        if (
            ref.codec != "tether-history-v1"
            or record.kind != "summary"
            or not record.summary
            or not record.summary.strip()
            or digest(record.model_dump(mode="json")) != ref.sha256
        ):
            raise ContextError("context_incompatible")
        await step.gov.authorize("read_session", step.gov.log.session.id)
        return record.summary
    except PolicyDenied:
        raise ContextError("context_denied") from None
    except (ValueError, TypeError, AttributeError):
        raise ContextError("context_unavailable") from None


def stored_ref(value: object) -> HistoryRef:
    if not isinstance(value, HistoryRef) or set(value.__dict__) != set(HistoryRef.model_fields):
        raise ContextError("context_incompatible")
    try:
        return HistoryRef.model_validate(value.model_dump(mode="json"))
    except (ValueError, TypeError, AttributeError):
        raise ContextError("context_incompatible") from None


async def persist(step: ContextState, text: str, key: str) -> HistoryRef:
    history = step.services.history
    if history is None:
        raise ContextError("context_unconfigured")
    record = HistoryRecord(kind="summary", summary=text)
    charge(record.model_dump(mode="json"), step.config.max_source_bytes)
    expected = digest(record.model_dump(mode="json"))
    content_key = key + ":" + expected
    ref = await step.executor.workers.call(
        step.gov.log.session.id,
        partial(
            history.put,
            actor=copy_actor(step.gov.actor),
            session_id=step.gov.log.session.id,
            key=content_key,
            record=record.model_copy(deep=True),
        ),
    )
    ref = stored_ref(ref)
    # Verify the provider returned a scoped ref to exactly the accepted content.
    if (
        ref.codec != "tether-history-v1"
        or ref.kind != "summary"
        or ref.component != "record"
        or ref.owner != step.gov.actor.id
        or ref.session_id != step.gov.log.session.id
        or ref.sha256 != expected
        or ref.provider != history.id
        or ref.key != content_key
    ):
        raise ContextError("context_incompatible")
    return ref.model_copy(deep=True)
