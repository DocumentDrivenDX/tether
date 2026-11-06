"""Session and payload read routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from ..auth import AuthContext
from ..serialization import model_response, to_wire_data
from ..wire import CallDetailRequest


def build_router(auth: AuthContext) -> APIRouter:
    router = APIRouter()

    @router.get("/sessions/{session_id}")
    async def get_session(
        request: Request, session_id: str, tier: Literal["live", "archive"] | None = None
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.get, session_id, tier=tier)
        return model_response(value, "SessionView")

    @router.get("/sessions/{session_id}/snapshot")
    async def snapshot(request: Request, session_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.snapshot, session_id)
        return model_response(value, "SessionSnapshot")

    @router.get("/sessions/{session_id}/entries")
    async def entries(
        request: Request,
        session_id: str,
        tier: Literal["live", "archive"] | None = None,
        start: int = 0,
        limit: int = 100,
        reverse: bool = False,
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(
            client.entries, session_id, tier=tier, start=start, limit=limit, reverse=reverse
        )
        return model_response(value, "EntryPage")

    @router.get("/sessions/{session_id}/history")
    async def history(
        request: Request,
        session_id: str,
        tier: Literal["live", "archive"] = "live",
        start: int = 0,
        limit: int = 100,
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.history, session_id, tier=tier, start=start, limit=limit)
        return model_response(value, "HistoryPage")

    @router.get("/sessions/{session_id}/pending")
    async def pending(request: Request, session_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.pending, session_id)
        return model_response(value, "PendingConfirmation")

    @router.get("/sessions/{session_id}/effects")
    async def effects(
        request: Request, session_id: str, start: int = 0, limit: int = 50
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.effects, session_id, start=start, limit=limit)
        return model_response(value, "EffectPage")

    @router.post("/sessions/{session_id}/call-details")
    async def call_detail(
        request: Request, session_id: str, body: CallDetailRequest
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        values = await auth.execute(client.call_detail, session_id, body.model_call_ids)
        return JSONResponse([to_wire_data(value, "CallDetail") for value in values])

    return router
