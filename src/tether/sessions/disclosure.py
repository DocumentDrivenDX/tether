"""Final aggregate permission checks before returning hydrated payloads."""

from __future__ import annotations

from tether.types.client import CallDetail, HistoryPage
from tether.types.execution import Actor
from tether.types.history import HistoryValue
from tether.types.storage import Page

from .access import SessionAccess
from .content import observation_status


def redact(value: HistoryValue) -> HistoryValue:
    if value.ref.kind != "model_exchange":
        return value
    return value.model_copy(update={"status": "denied", "record": None, "reason": "policy_denied"})


async def history(
    access: SessionAccess, actor: Actor, session_id: str, page: HistoryPage
) -> HistoryPage:
    if not any(slot.value.ref.kind == "model_exchange" for slot in page.items):
        return page
    if await access.allowed(actor, session_id, "read_call_payload"):
        return page
    return page.model_copy(
        update={
            "items": tuple(
                slot.model_copy(update={"value": redact(slot.value)}) for slot in page.items
            )
        }
    )


async def calls(
    access: SessionAccess, actor: Actor, session_id: str, page: Page[CallDetail]
) -> Page[CallDetail]:
    if await access.allowed(actor, session_id, "read_call_payload"):
        return page
    items: list[CallDetail] = []
    for item in page.items:
        values = [redact(value) for value in item.observations]
        items.append(
            item.model_copy(
                update={"observations": tuple(values), "status": observation_status(values)}
            )
        )
    return page.model_copy(update={"items": tuple(items)})
