"""Bounded, currently authorized original snapshots; no SDK history is authoritative."""

from dataclasses import dataclass

from tether.context.provenance import SourceProjection
from tether.context.validation import copied
from tether.types import JsonValue, Message
from tether.types.canonical import canonical, digest
from tether.types.context import ContextError, ContextSource
from tether.types.execution import Entry, ExecutionDenied

from ..effects import unresolved_invocations
from ..governance import Governance


def charge(value: JsonValue, capacity: int, used: int = 0) -> int:
    size = used + len(canonical(value))
    if size > capacity:
        raise ContextError("context_exhausted")
    return size


def _wire_message(message: Message) -> None:
    if message.tool_call_id is not None and message.role != "tool":
        raise ContextError("context_invalid")
    if message.role == "tool" and message.tool_calls:
        raise ContextError("context_invalid")


def groups(
    sources: tuple[ContextSource, ...],
) -> tuple[tuple[tuple[int, ...], ...], frozenset[int]]:
    result: list[tuple[int, ...]] = []
    current: list[int] = []
    pending: set[str] = set()
    for source in sources:
        message = source.message
        current.append(source.index)
        _wire_message(message)
        if message.tool_calls:
            ids = tuple(c.id for c in message.tool_calls)
            if message.role != "assistant" or pending or len(set(ids)) != len(ids):
                raise ContextError("context_invalid")
            pending.update(ids)
        elif message.role == "tool":
            if message.tool_call_id not in pending:
                raise ContextError("context_invalid")
            pending.remove(message.tool_call_id)
        elif message.tool_call_id is not None:
            raise ContextError("context_invalid")
        if not pending:
            result.append(tuple(current))
            current = []
    protected = frozenset(current)
    if current:
        result.append(tuple(current))
    return tuple(result), protected


@dataclass(frozen=True)
class Snapshot:
    entries: tuple[Entry, ...]
    sources: tuple[ContextSource, ...]
    groups: tuple[tuple[int, ...], ...]
    protected: frozenset[int]
    pending: int
    effects: int

    def narrowed(self, projection: SourceProjection) -> tuple[tuple[int, ...], ...]:
        kept = {s.index for s in projection.sources}
        if self.protected - kept:
            raise ContextError("context_invalid")
        result: list[tuple[int, ...]] = []
        for group in self.groups:
            remaining = tuple(i for i in group if i in kept)
            if remaining and remaining != group:
                raise ContextError("context_invalid")
            if remaining:
                result.append(remaining)
        return tuple(result)

    def view_digest(self, projection: SourceProjection) -> str:
        return digest(
            {
                "sources": [s.model_dump(mode="json") for s in projection.sources],
                "catalog": [
                    {
                        "index": e.index,
                        "ref": e.history_ref.model_dump(mode="json")
                        if e.history_ref is not None
                        else None,
                    }
                    for e in self.entries
                ],
                "filtered_indices": list(projection.filtered_indices),
            }
        )


def _pending(entries: tuple[Entry, ...]) -> set[str]:
    result: set[str] = set()
    for entry in entries:
        binding = entry.facts.get("binding")
        if entry.kind == "confirmation" and isinstance(binding, dict):
            inv = binding.get("invocation_id")
            if isinstance(inv, str):
                if entry.facts.get("state") == "requested":
                    result.add(inv)
                else:
                    result.discard(inv)
    return result


async def capture(gov: Governance, capacity: int, turns: frozenset[str]) -> Snapshot:
    entries = tuple(e for e in gov.log.entries if e.kind == "message")
    used = 0
    values: list[ContextSource] = []
    for entry in entries:
        used = charge(entry.model_dump(mode="json"), capacity, used)
        try:
            message = await gov.read(entry.history_ref, source_index=entry.index)
        except ExecutionDenied as error:
            if error.reason in {"history_unavailable", "history_pending"}:
                raise ContextError(
                    "history_pending"
                    if error.reason == "history_pending"
                    else "history_unavailable"
                ) from None
            raise
        source = copied(
            ContextSource, ContextSource.model_construct(index=entry.index, message=message)
        )
        used = charge(source.model_dump(mode="json"), capacity, used)
        values.append(source)
    sources = tuple(values)
    partition, protected = groups(sources)
    pending = _pending(tuple(gov.log.entries))
    effects = unresolved_invocations(gov.log.entries)
    guarded = pending | effects.keys()
    required = set(protected)
    current_users = [
        s.index
        for s, e in zip(sources, entries, strict=True)
        if s.message.role == "user" and e.turn_id in turns
    ]
    if current_users:
        required.add(current_users[-1])
    if partition:
        required.update(partition[-1])
    for entry in entries:
        if any(
            digest([gov.log.session.id, entry.index, m.position]) in guarded
            for m in entry.call_manifest
        ):
            required.add(entry.index)
    for group in partition:
        if required.intersection(group):
            required.update(group)
    return Snapshot(entries, sources, partition, frozenset(required), len(pending), len(effects))
