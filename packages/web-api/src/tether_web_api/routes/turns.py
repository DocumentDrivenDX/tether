"""Turn command and process-independent result routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from ..auth import AuthContext
from ..serialization import model_response
from ..settings import ApiSettings
from ..wire import AcceptedTurn, ConfirmRequest, ConfirmResponse, ResolveRequest, SendRequest


def build_router(auth: AuthContext, settings: ApiSettings) -> APIRouter:
    router = APIRouter()

    def accepted(
        request: Request, session_id: str, turn_id: str, request_id: str | None
    ) -> AcceptedTurn:
        root_path = request.scope.get("root_path", "").rstrip("/")
        return AcceptedTurn(
            request_id=request_id,
            turn_id=turn_id,
            result_url=f"{root_path}{settings.api_prefix}/sessions/{session_id}/turns/{turn_id}",
        )

    @router.post("/sessions/{session_id}/turns", status_code=202)
    async def send_turn(request: Request, session_id: str, body: SendRequest) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        handle = await auth.execute(
            client.send,
            session_id,
            body.text,
            request_id=body.request_id,
            attachments=body.attachments,
            spec=body.spec,
            locator={"session_id": session_id, "request_id": body.request_id},
        )
        return model_response(
            accepted(request, session_id, handle.turn_id, body.request_id),
            "AcceptedTurn",
            status_code=202,
        )

    @router.get("/sessions/{session_id}/turns/{turn_id}")
    async def turn_result(request: Request, session_id: str, turn_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(
            client.turn_result,
            session_id,
            turn_id,
            locator={"session_id": session_id, "turn_id": turn_id},
        )
        return model_response(value, "TurnResult")

    @router.delete("/sessions/{session_id}/turns/{turn_id}")
    async def cancel_turn(request: Request, session_id: str, turn_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        return JSONResponse(
            await auth.execute(
                client.cancel_turn,
                session_id,
                turn_id,
                locator={"session_id": session_id, "turn_id": turn_id},
            )
        )

    @router.post("/sessions/{session_id}/cancel")
    async def cancel_current_turn(request: Request, session_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        return JSONResponse(
            await auth.execute(client.cancel, session_id, locator={"session_id": session_id})
        )

    @router.post("/sessions/{session_id}/confirmations/{invocation_id}")
    async def confirm(
        request: Request,
        session_id: str,
        invocation_id: str,
        body: ConfirmRequest,
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        handle = await auth.execute(
            client.confirm,
            session_id,
            invocation_id,
            body.answer,
            locator={"session_id": session_id, "invocation_id": invocation_id},
        )
        if handle is not None:
            return model_response(
                accepted(request, session_id, handle.turn_id, None), "AcceptedTurn", status_code=202
            )
        state = await auth.execute(client.get, session_id, locator={"session_id": session_id})
        return model_response(ConfirmResponse(state=state), "ConfirmResponse")

    @router.post("/sessions/{session_id}/resume", status_code=202)
    async def resume(request: Request, session_id: str) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        handle = await auth.execute(client.resume, session_id, locator={"session_id": session_id})
        return model_response(
            accepted(request, session_id, handle.turn_id, None), "AcceptedTurn", status_code=202
        )

    @router.post("/sessions/{session_id}/effects/{invocation_id}/resolve")
    async def resolve(
        request: Request,
        session_id: str,
        invocation_id: str,
        body: ResolveRequest,
    ) -> JSONResponse:
        _actor, client = await auth.resolve(request)
        value = await auth.execute(
            client.resolve,
            session_id,
            invocation_id,
            body.outcome,
            reason=body.reason,
            locator={"session_id": session_id, "invocation_id": invocation_id},
        )
        return model_response(value, "SessionSummary")

    return router
