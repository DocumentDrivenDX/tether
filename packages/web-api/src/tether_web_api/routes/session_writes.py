"""Session metadata and lifecycle routes."""

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from ..auth import AuthContext
from ..serialization import model_response
from ..wire import HistoryLinkRequest, UpdateSessionRequest


def build_router(auth: AuthContext) -> APIRouter:
    router = APIRouter()

    @router.patch("/sessions/{session_id}")
    async def update_session(
        request: Request, session_id: str, body: UpdateSessionRequest
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        changes = body.model_dump(exclude_unset=True)
        value = await auth.execute(
            client.update, session_id, **changes, locator={"session_id": session_id}
        )
        return model_response(value, "SessionSummary")

    @router.post("/sessions/{session_id}/close")
    async def close_session(request: Request, session_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.close, session_id, locator={"session_id": session_id})
        return model_response(value, "SessionSummary")

    @router.post("/sessions/{session_id}/history-links")
    async def link_history(
        request: Request, session_id: str, body: HistoryLinkRequest
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(
            client.link_history,
            session_id,
            int(body.message_index),
            body.previous,
            body.verified,
            key=body.key,
            locator={"session_id": session_id},
        )
        return model_response(value, "Entry")

    @router.post("/sessions/{session_id}/restore")
    async def restore(request: Request, session_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(client.restore, session_id, locator={"session_id": session_id})
        return model_response(value, "SessionSummary")

    return router
