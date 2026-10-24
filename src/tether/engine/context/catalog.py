"""Accepted summary provenance and current source compatibility, never cached authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tether.context.validation import copied, indices
from tether.types import JsonValue
from tether.types.canonical import digest
from tether.types.context import (
    CompactionRecord,
    ContextError,
    ContextInput,
    ContextPreparation,
    ContextSummary,
    SourceRange,
)
from tether.types.execution import Entry

from .sources import charge

if TYPE_CHECKING:
    from .state import ContextState


def range_digest(step: ContextState, context: ContextInput, ranges: tuple[SourceRange, ...]) -> str:
    selected = set(indices(context, ranges))
    original = step.original
    if original is None:
        raise ContextError("context_invalid")
    refs = {e.index: e.history_ref for e in original.entries}
    value: list[JsonValue] = []
    for source in context.sources:
        if source.index not in selected:
            continue
        ref = refs[source.index]
        value.append(
            {
                "index": source.index,
                "message": source.message.model_dump(mode="json"),
                "ref": ref.model_dump(mode="json") if ref is not None else None,
            }
        )
    charge(value, step.config.max_source_bytes)
    return digest(value)


def _provenance(
    entry: Entry,
    record: CompactionRecord,
    entries: tuple[Entry, ...],
    preparations: dict[int, tuple[Entry, ContextPreparation]],
) -> None:
    prior = preparations.get(record.preparation_ordinal)
    calls = [
        e
        for e in entries
        if e.kind == "model_call" and e.facts.get("model_call_id") == record.model_call_id
    ]
    if prior is None or len(calls) != 1:
        raise ContextError("context_incompatible")
    allocation, prep = prior
    call = calls[0]
    if (
        entry.key != f"context:summary:{record.preparation_ordinal}:{record.part_position}"
        or allocation.index >= entry.index
        or entry.index <= record.through_index
        or call.turn_id != entry.turn_id
        or allocation.turn_id != entry.turn_id
        or prep.model_call_id == record.model_call_id
        or (call.facts.get("status"), call.facts.get("purpose")) != ("ok", "summary")
        or call.facts.get("preparation_ordinal") != record.preparation_ordinal
        or call.facts.get("part_position") != record.part_position
        or call.facts.get("compaction_key") != entry.key
        or (
            prep.through_index,
            prep.config_digest,
            prep.scope_digest,
            prep.spec_digest,
            prep.summary_profile_digest,
            prep.strategy_id,
            prep.strategy_version,
        )
        != (
            record.through_index,
            record.config_digest,
            record.scope_digest,
            record.spec_digest,
            record.summary_profile_digest,
            record.strategy_id,
            record.strategy_version,
        )
    ):
        raise ContextError("context_incompatible")


def accepted(step: ContextState) -> tuple[ContextSummary, ...]:
    entries = tuple(step.gov.log.entries)
    preparations: dict[int, tuple[Entry, ContextPreparation]] = {}
    result: dict[int, ContextSummary] = {}
    used = 0
    try:
        for entry in entries:
            if entry.kind == "context_preparation":
                prep = ContextPreparation.model_validate(entry.facts)
                preparations[prep.ordinal] = entry, prep
            elif entry.kind == "compaction":
                used = charge(entry.model_dump(mode="json"), step.config.max_source_bytes, used)
                record = copied(CompactionRecord, CompactionRecord.model_validate(entry.facts))
                _provenance(entry, record, entries, preparations)
                summary = ContextSummary(index=entry.index, record=record)
                if record.predecessor is not None:
                    previous = result.get(record.predecessor)
                    if previous is None or not all(
                        any(r.start <= p.start and p.end <= r.end for r in record.source_ranges)
                        for p in previous.record.source_ranges
                    ):
                        raise ContextError("context_incompatible")
                result[entry.index] = summary
    except (ValueError, TypeError, AttributeError):
        raise ContextError("context_incompatible") from None
    return tuple(result.values())


def compatible(step: ContextState, context: ContextInput) -> tuple[ContextSummary, ...]:
    result: list[ContextSummary] = []
    profile = step.summary_profile()
    for summary in accepted(step):
        record = summary.record
        if profile is None or (
            record.config_digest,
            record.scope_digest,
            record.spec_digest,
            record.strategy_id,
            record.strategy_version,
            record.profile_id,
            record.profile_revision,
            record.summary_profile_digest,
            record.summary_profile_id,
            record.summary_profile_revision,
            record.summary_ref.owner,
            record.summary_ref.session_id,
        ) != (
            digest(step.config.model_dump(mode="json")),
            step.scope_digest(),
            digest(step.executor.spec.model_dump(mode="json")),
            step.strategy_id,
            step.strategy_version,
            step.profile.profile_id,
            step.profile.revision,
            digest(profile.model_dump(mode="json")),
            profile.profile_id,
            profile.revision,
            step.gov.actor.id,
            context.session_id,
        ):
            continue
        try:
            covered = set(indices(context, record.source_ranges))
            if (
                covered & context.protected
                or any(
                    covered.intersection(g.indices) and not set(g.indices).issubset(covered)
                    for g in context.groups
                )
                or range_digest(step, context, record.source_ranges) != record.source_view_digest
            ):
                continue
        except ContextError:
            continue
        result.append(summary)
    return tuple(result)
