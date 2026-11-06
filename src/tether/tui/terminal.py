"""Bounded reconstruction of the selected session's latest terminal notice."""

from __future__ import annotations

from collections.abc import Callable

from tether.client import ClientProtocol, Entry, ReadLimit, SessionSnapshot

_PAGE_SIZE = 32
_PAGE_BUDGET = 8
_ENTRY_BUDGET = 256
_STOP_LABELS = {
    "final": "[final]",
    "confirmation_required": "[confirmation_required]",
    "cancelled": "[cancelled]",
    "interrupted": "[interrupted]",
    "step_limit": "[step_limit]",
    "model_denied": "[model_denied]",
    "model_unavailable": "[model_unavailable]",
    "model_incomplete": "[model_incomplete]",
    "context_unavailable": "[context_unavailable]",
}
_UNAVAILABLE = "[terminal_status_unavailable]"


async def terminal_notice(
    client: ClientProtocol,
    session_id: str,
    snapshot: SessionSnapshot,
    *,
    current: Callable[[], bool] | None = None,
) -> str | None:
    """Read a stop label from a bounded, snapshot-pinned journal prefix."""
    if snapshot.through_index < 0 or not _is_current(current):
        return None
    return await _scan_entries(client, session_id, snapshot, current)


def _is_current(current: Callable[[], bool] | None) -> bool:
    return current is None or current()


def _lifecycle_notice(entries: tuple[Entry, ...]) -> tuple[bool, str | None]:
    for entry in entries:
        if entry.kind in {"turn_started", "turn_resumed"}:
            return True, None
        if entry.kind == "turn_ended":
            reason = entry.facts.get("stop_reason")
            label = _STOP_LABELS.get(reason, _UNAVAILABLE) if type(reason) is str else _UNAVAILABLE
            return True, label
    return False, None


def _page_lifecycle_notice(
    entries: tuple[Entry, ...], through_index: int, start: int, limit: int, partial: bool
) -> tuple[bool, str | None]:
    if partial or len(entries) > limit:
        return True, _UNAVAILABLE
    previous = start + 1
    for entry in entries:
        if entry.index < 0 or entry.index > through_index or entry.index != previous - 1:
            return True, _UNAVAILABLE
        previous = entry.index
    return _lifecycle_notice(entries)


async def _scan_entries(
    client: ClientProtocol,
    session_id: str,
    snapshot: SessionSnapshot,
    current: Callable[[], bool] | None,
) -> str | None:
    start = snapshot.through_index
    remaining = _ENTRY_BUDGET
    notice: str | None = _UNAVAILABLE
    for _ in range(_PAGE_BUDGET):
        if remaining <= 0:
            break
        if not _is_current(current):
            return None
        limit = min(_PAGE_SIZE, remaining)
        try:
            page = await client.entries(session_id, start=start, limit=limit, reverse=True)
        except ReadLimit:
            notice = _UNAVAILABLE
            break
        if not _is_current(current):
            return None
        found, notice = _page_lifecycle_notice(
            page.items, snapshot.through_index, start, limit, page.partial
        )
        if found:
            break
        remaining -= len(page.items)
        if not page.items:
            notice = _UNAVAILABLE
            break
        if page.next_start is None:
            notice = None
            break
        if (
            page.next_start < 0
            or page.next_start > snapshot.through_index
            or page.next_start >= start
            or page.next_start != page.items[-1].index - 1
        ):
            notice = _UNAVAILABLE
            break
        start = page.next_start
    else:
        notice = _UNAVAILABLE

    return notice
