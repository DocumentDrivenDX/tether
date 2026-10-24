"""Linear ordered provenance checks never infer source identity from redacted text."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import cast

from tether.types import Message
from tether.types.canonical import canonical
from tether.types.context import ContextError, ContextSource

from .validation import copied


@dataclass(frozen=True)
class SourceProjection:
    sources: tuple[ContextSource, ...]
    filtered_indices: tuple[int, ...]


def structural(message: Message) -> bytes:
    return canonical(message.model_dump(mode="json", exclude={"content", "attachments"}))


def compatible(source: Message, candidate: Message) -> bool:
    if structural(source) != structural(candidate):
        return False
    allowed = iter(canonical(a.model_dump(mode="json")) for a in source.attachments)
    return all(
        any(canonical(a.model_dump(mode="json")) == original for original in allowed)
        for a in candidate.attachments
    )


def _mapping(
    sources: tuple[ContextSource, ...],
    outputs: tuple[Message, ...],
    identities: tuple[int | None, ...],
    *,
    reverse: bool,
) -> tuple[int, ...]:
    selected: list[int] = []
    position = len(sources) - 1 if reverse else 0
    step = -1 if reverse else 1
    order = reversed(range(len(outputs))) if reverse else range(len(outputs))
    for number in order:
        fixed = identities[number]
        if fixed is not None:
            if (fixed - position) * step < 0:
                raise ContextError("context_invalid")
            position = fixed
        while 0 <= position < len(sources):
            if compatible(sources[position].message, outputs[number]):
                break
            if fixed is not None:
                raise ContextError("context_invalid")
            position += step
        if not 0 <= position < len(sources):
            raise ContextError("context_invalid")
        selected.append(position)
        position += step
    return tuple(reversed(selected)) if reverse else tuple(selected)


def align_sources(
    sources: tuple[ContextSource, ...],
    supplied: tuple[Message, ...],
    narrowed: object,
) -> SourceProjection:
    if len(sources) != len(supplied) or not isinstance(narrowed, tuple):
        raise ContextError("context_invalid")
    sources = tuple(copied(ContextSource, source) for source in sources)
    if any(a.index >= b.index for a, b in pairwise(sources)):
        raise ContextError("context_invalid")
    by_identity = {id(message): position for position, message in enumerate(supplied)}
    if len(by_identity) != len(supplied):
        raise ContextError("context_invalid")
    candidates = cast(tuple[object, ...], narrowed)
    if not all(isinstance(message, Message) for message in candidates):
        raise ContextError("context_invalid")
    messages = cast(tuple[Message, ...], candidates)
    try:
        outputs = tuple(
            ContextSource(index=0, message=m).message.model_copy(deep=True) for m in messages
        )
        identities = tuple(by_identity.get(id(message)) for message in messages)
        first = _mapping(sources, outputs, identities, reverse=False)
        if first != _mapping(sources, outputs, identities, reverse=True):
            raise ContextError("context_invalid")
        kept = tuple(
            ContextSource(index=sources[p].index, message=m)
            for p, m in zip(first, outputs, strict=True)
        )
    except (ValueError, TypeError, AttributeError):
        raise ContextError("context_invalid") from None
    selected = {s.index for s in kept}
    return SourceProjection(kept, tuple(s.index for s in sources if s.index not in selected))
