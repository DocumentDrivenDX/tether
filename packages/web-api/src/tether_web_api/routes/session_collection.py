"""Collection queries and creation routes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from tether.client import SessionQuery

from ..auth import AuthContext
from ..serialization import model_response
from ..wire import CreateSessionRequest


def build_router(auth: AuthContext) -> APIRouter:
    router = APIRouter()

    @router.get("/capabilities")
    async def capabilities(request: Request) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        return model_response(client.capabilities, "ClientCapabilities")

    @router.get("/sessions")
    async def list_sessions(
        request: Request,
        ref: str | None = None,
        status: list[str] | None = None,
        tag: list[str] | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        text: str | None = None,
        order: Literal["updated_desc", "created_desc", "created_asc"] = "updated_desc",
        tier: Literal["live", "archive"] = "live",
        cursor: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        actor, client = await auth.resolve(request)
        query = SessionQuery(
            owner=actor.id,
            ref=ref,
            status=frozenset(status) if status else None,
            tags_all=tuple(tag or ()),
            updated_after=updated_after,
            updated_before=updated_before,
            text=text,
            order=order,
        )
        value = await auth.execute(client.list, query, tier=tier, cursor=cursor, limit=limit)
        return model_response(value, "PageSessionSummary")

    @router.post("/sessions", status_code=201)
    async def create_session(request: Request, body: CreateSessionRequest) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(
            client.create,
            ref=body.request_id,
            spec=body.spec,
            title=body.title,
            tags=body.tags,
            settings=body.settings,
            locator={"request_id": body.request_id},
        )
        return model_response(value, "SessionSummary", status_code=201)

    @router.get("/sessions/by-ref/{ref:path}")
    async def find_session(request: Request, ref: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.find, ref)
        return model_response(value, "SessionSummary")

    return router
