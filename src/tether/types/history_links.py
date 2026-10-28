"""Pure accepted-journal selection of equivalent history replacements."""

from tether.types.canonical import canonical
from tether.types.execution import Entry, HistoryRef
from tether.types.history import HistoryLink
from tether.types.storage import IntegrityError


def select(
    original: HistoryRef,
    message_index: int,
    entries: tuple[Entry, ...],
    *,
    through_index: int,
    max_hops: int,
) -> HistoryRef:
    """Choose the newest reachable accepted replacement at one pinned journal head."""

    current = original
    active: dict[bytes, HistoryLink] = {}
    for entry in sorted(entries, key=lambda item: item.index):
        if entry.index > through_index:
            continue
        if entry.kind != "history_linked" or entry.index <= message_index:
            raise IntegrityError("history_link_integrity")
        try:
            link = HistoryLink.model_validate(entry.facts)
        except ValueError:
            raise IntegrityError("history_link_integrity") from None
        if (
            link.message_index != message_index
            or link.previous.owner != original.owner
            or link.previous.session_id != original.session_id
            or link.previous.kind != original.kind
            or link.previous.component != original.component
            or link.previous.codec != original.codec
            or link.canonical_digest != original.sha256
        ):
            raise IntegrityError("history_link_integrity")
        active[canonical(link.previous.model_dump(mode="json"))] = link
    seen: set[bytes] = set()
    for _ in range(max_hops + 1):
        key = canonical(current.model_dump(mode="json"))
        if key in seen:
            raise IntegrityError("history_link_cycle")
        seen.add(key)
        link = active.get(key)
        if link is None:
            return current
        current = link.verified
    raise IntegrityError("history_link_hops")
