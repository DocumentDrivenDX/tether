"""Read operations for the optional HTTP client."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from tether.client import (
    CallDetail,
    EffectPage,
    EntryPage,
    HistoryPage,
    Page,
    PendingConfirmation,
    SessionQuery,
    SessionSnapshot,
    SessionSummary,
    SessionView,
)

from .base import RemoteOperations
from .wire import append_param, datetime_value, decode, segment


class RemoteReads(RemoteOperations):
    """Read methods implementing the transport-neutral client contract."""

    async def list(
        self,
        query: SessionQuery | None = None,
        *,
        tier: Literal["live", "archive"] = "live",
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[SessionSummary]:
        params: list[tuple[str, str]] = [("tier", tier), ("limit", str(limit))]
        if cursor is not None:
            params.append(("cursor", cursor))
        if query is not None:
            append_param(params, "ref", query.ref)
            params.extend(("status", item) for item in sorted(query.status or ()))
            params.extend(("tag", item) for item in query.tags_all)
            append_param(params, "updated_after", datetime_value(query.updated_after))
            append_param(params, "updated_before", datetime_value(query.updated_before))
            append_param(params, "text", query.text)
            params.append(("order", query.order))
        return await self._model("GET", "/sessions", Page[SessionSummary], params=params)

    async def get(
        self, session_id: str, *, tier: Literal["live", "archive"] | None = None
    ) -> SessionView:
        params = {"tier": tier} if tier is not None else None
        return await self._model(
            "GET", f"/sessions/{segment(session_id)}", SessionView, params=params
        )

    async def entries(
        self,
        session_id: str,
        *,
        tier: Literal["live", "archive"] | None = None,
        start: int = 0,
        limit: int = 100,
        reverse: bool = False,
    ) -> EntryPage:
        return await self._model(
            "GET",
            f"/sessions/{segment(session_id)}/entries",
            EntryPage,
            params={
                key: value
                for key, value in {
                    "tier": tier,
                    "start": start,
                    "limit": limit,
                    "reverse": reverse,
                }.items()
                if value is not None
            },
        )

    async def history(
        self,
        session_id: str,
        *,
        tier: Literal["live", "archive"] = "live",
        start: int = 0,
        limit: int = 100,
    ) -> HistoryPage:
        return await self._model(
            "GET",
            f"/sessions/{segment(session_id)}/history",
            HistoryPage,
            params={"tier": tier, "start": start, "limit": limit},
        )

    async def pending(self, session_id: str) -> PendingConfirmation | None:
        value = await self._request("GET", f"/sessions/{segment(session_id)}/pending")
        return decode(PendingConfirmation, value) if value is not None else None

    async def snapshot(self, session_id: str) -> SessionSnapshot:
        return await self._model(
            "GET", f"/sessions/{segment(session_id)}/snapshot", SessionSnapshot
        )

    async def effects(self, session_id: str, *, start: int = 0, limit: int = 50) -> EffectPage:
        return await self._model(
            "GET",
            f"/sessions/{segment(session_id)}/effects",
            EffectPage,
            params={"start": start, "limit": limit},
        )

    async def call_detail(
        self, session_id: str, model_call_ids: Sequence[str]
    ) -> tuple[CallDetail, ...]:
        values = await self._request(
            "POST",
            f"/sessions/{segment(session_id)}/call-details",
            json_body={"model_call_ids": list(model_call_ids)},
        )
        return tuple(decode(CallDetail, value) for value in values)

    async def find(self, ref: str) -> SessionSummary | None:
        value = await self._request("GET", f"/sessions/by-ref/{segment(ref)}")
        return decode(SessionSummary, value) if value is not None else None
